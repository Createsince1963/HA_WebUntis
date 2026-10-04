"""
WebUntis API v2 - FastAPI backend.

Contract (shared by the Python GUI and the Android app):
  GET  /api/health
  POST /api/auth/validate          -> {status, access_token, user_id, user_name, message}
  GET  /api/users/my/timetable     (Authorization: Bearer <token>)
  GET  /api/schools/{id}/classes
  GET  /api/schools/{id}/timetable
  GET  /api/data/teachers | substitutions | holidays | absences
All data endpoints answer {status: bool, data: [...], message: str}.
"""
import json
import logging
import os
import secrets
import socket
import threading
from pathlib import Path
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import requests
import webuntis
import rest_timetable
import extras
from dotenv import load_dotenv
from fastapi import FastAPI, Header
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "DEBUG").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("webuntis_api")

USER_AGENT = "Untis-HUB/0.4.0"
SESSION_TTL = timedelta(hours=8)

app = FastAPI(title="WebUntis API v2", version="2.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    server: str
    school: str
    username: str
    password: str


# token -> {"session": webuntis.Session, "user": str, "created": datetime, "lock": Lock}
SESSIONS: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------- helpers

def _server_url(server: str) -> str:
    """'host:8080' -> http://host:8080, 'host' -> host (library uses https)."""
    server = server.strip().rstrip("/")
    if server.startswith(("http://", "https://")):
        return server
    host, _, port = server.partition(":")
    if port and port not in ("443",):
        return f"http://{host}:{port}"
    return host


def _server_host(server: str) -> str:
    value = server.strip().rstrip("/")
    if "://" in value:
        value = value.split("://", 1)[1]
    return value.split("/", 1)[0].split(":", 1)[0].lower()


def _resolve_school_login(server: str, school: str) -> str:
    """WebUntis JSON-RPC needs the school loginName, not the display name."""
    school = school.strip()
    if not school or school.lower() in DEMO_HOSTS:
        return school
    try:
        result = school_search(school)
        wanted_host = _server_host(server)
        candidates = result.get("data") or []
        exact = []
        same_host = []
        for item in candidates:
            display = str(item.get("displayName") or "")
            login_name = str(item.get("loginName") or "")
            item_host = _server_host(str(item.get("server") or ""))
            if display.casefold() == school.casefold() or login_name.casefold() == school.casefold():
                exact.append(item)
            if wanted_host and item_host == wanted_host:
                same_host.append(item)
        for item in same_host + exact + candidates:
            login_name = str(item.get("loginName") or "").strip()
            if login_name:
                if login_name != school:
                    log.info("Resolved school display name %r to loginName %r", school, login_name)
                return login_name
    except Exception:
        log.info("School loginName resolution failed for %r", school, exc_info=True)
    return school


def _fail(message: str) -> Dict[str, Any]:
    return {"status": False, "data": None, "message": message}


def _ok(data: List[Any], **extra: Any) -> Dict[str, Any]:
    return {"status": True, "data": data, "message": f"{len(data)} entries", **extra}


def _token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    return parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else authorization.strip()


def _get_entry(authorization: Optional[str]) -> Optional[Dict[str, Any]]:
    tok = _token(authorization)
    entry = SESSIONS.get(tok) if tok else None
    if entry and datetime.now() - entry["created"] > SESSION_TTL:
        SESSIONS.pop(tok, None)
        return None
    return entry


def _names(items: Any) -> str:
    try:
        return ", ".join(str(getattr(i, "name", i)) for i in items)
    except Exception:
        return ""


def _guarded(authorization: Optional[str], what: str,
             fn: Callable[[webuntis.Session], List[Any]]) -> Dict[str, Any]:
    entry = _get_entry(authorization)
    if not entry:
        return _fail("Not logged in or session expired")
    try:
        with entry["lock"]:
            result = fn(entry["session"])
        if isinstance(result, tuple):
            return _ok(result[0], **result[1])
        return _ok(result)
    except Exception as exc:  # WebUntis errors are varied; report without leaking details
        log.exception("%s failed", what)
        return _fail(f"{what} failed: {exc}")


def _teacher_map(s: Any, first: date, last: date) -> Dict[int, str]:
    """Students may not call getTeachers (error -8509). The timetable itself can return teacher
    short names when 'teacherFields' asks for them -> build an id -> name map from that."""
    try:
        login = getattr(s, "login_result", {}) or {}
        params = {"options": {
            "element": {"id": str(login.get("personId")), "type": str(login.get("personType", 5))},
            "startDate": int(first.strftime("%Y%m%d")), "endDate": int(last.strftime("%Y%m%d")),
            "teacherFields": ["id", "name"]}}
        result = s._request("getTimetable", params) or []
        out: Dict[int, str] = {}
        for period in result:
            for t in period.get("te", []) or []:
                if t.get("id") is not None and t.get("name"):
                    out[t["id"]] = str(t["name"])
                if t.get("orgid") is not None and t.get("orgname"):
                    out[t["orgid"]] = str(t["orgname"])
        return out
    except Exception:
        log.info("teacher map via teacherFields unavailable", exc_info=True)
        return {}


def _period_teachers(p: Any, tmap: Dict[int, str]) -> str:
    names = _names(p.teachers)
    if names:
        return names
    try:
        raw = getattr(p, "_data", {}).get("te", []) or []
        found = [t.get("name") or tmap.get(t.get("id")) for t in raw]
        return ", ".join(n for n in found if n)
    except Exception:
        return ""


def _period_row(p: Any, tmap: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    code = getattr(p, "code", None)
    return {
        "id": int(getattr(p, "id", 0) or 0),
        "date": p.start.date().isoformat(),
        "startTime": f"{p.start:%a %d.%m. %H:%M}",
        "endTime": f"{p.end:%H:%M}",
        "subject": _names(p.subjects),
        "teacher": _period_teachers(p, tmap or {}),
        "classroom": _names(p.rooms),
        "substitution": "irregular" if code == "irregular" else None,
        "cancelled": code == "cancelled",
        "exam": str(getattr(p, "type", "") or "") == "ex",
        "title": str(getattr(p, "lstext", "") or getattr(p, "substText", "") or ""),
    }


# ---------------------------------------------------------------- offline demo mode
# Used when the server is "demo.local": no network needed, deterministic sample data.

DEMO_HOSTS = ("demo.local", "demo", "mock")


class _N(SimpleNamespace):
    def __int__(self) -> int:
        return int(self.id)


class DemoSession:
    """Mimics the small part of webuntis.Session that this backend uses."""

    login_result = {"personType": 5, "personId": 1001}
    _SUBJECTS = ["Mathematik", "Deutsch", "Englisch", "Physik", "Informatik", "Geschichte"]
    _TEACHERS = [("MUE", "Mueller Anna"), ("SCH", "Schmidt Peter"), ("BAU", "Bauer Eva"),
                 ("WEB", "Weber Tom"), ("FIS", "Fischer Lena"), ("KOC", "Koch Jan")]

    def logout(self, suppress_errors: bool = False) -> None:
        return None

    def _teachers(self):
        return [_N(id=i + 1, name=n, full_name=f, long_name=f) for i, (n, f) in enumerate(self._TEACHERS)]

    def teachers(self):
        return self._teachers()

    def klassen(self):
        t = self._teachers()
        return [_N(id=1, name="3A", teacher1=t[0]), _N(id=2, name="3B", teacher1=t[1]),
                _N(id=3, name="4A", teacher1=t[2])]

    def _periods(self, start: date, end: date, substitution_only: bool = False):
        rooms = ["R101", "R102", "PHY1", "EDV2"]
        teachers = self._teachers()
        rows, pid, day = [], 1, start
        while day <= end:
            if day.weekday() < 5:
                for i in range(6):
                    k = (day.toordinal() + i) % len(self._SUBJECTS)
                    begin = datetime(day.year, day.month, day.day, 8, 0) + timedelta(minutes=50 * i)
                    code = "cancelled" if (k == 3 and i == 4) else ("irregular" if (k == 1 and i == 2) else None)
                    if substitution_only and code != "irregular":
                        pid += 1
                        continue
                    rows.append(_N(
                        id=pid, start=begin, end=begin + timedelta(minutes=45), code=code,
                        subjects=[_N(name=self._SUBJECTS[k])],
                        teachers=[teachers[(k + 1) % len(teachers)] if code == "irregular" else teachers[k]],
                        original_teachers=[teachers[k]] if code == "irregular" else [],
                        rooms=[_N(name=rooms[k % len(rooms)])]))
                    pid += 1
            day += timedelta(days=1)
        return rows

    def my_timetable(self, start: date, end: date):
        return self._periods(start, end)

    def timetable(self, klasse: Any = None, start: date = None, end: date = None, **_: Any):
        return self._periods(start, end)

    def substitutions(self, start: date, end: date, department_id: int = 0):
        return self._periods(start, end, substitution_only=True)

    def holidays(self):
        y = date.today().year
        return [_N(id=1, name="Herbstferien", start=date(y, 10, 26), end=date(y, 10, 31)),
                _N(id=2, name="Weihnachtsferien", start=date(y, 12, 24), end=date(y + 1, 1, 6)),
                _N(id=3, name="Sommerferien", start=date(y + 1, 7, 4), end=date(y + 1, 9, 7))]


MAX_RANGE_DAYS = 62   # own safety cap (about two months); WebUntis documents no limit
CHUNK_DAYS = 7        # fetch week by week to keep every single request small


def _parse_day(value: Optional[str], default: date) -> date:
    if not value:
        return default
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return default


def _resolve_range(start: Optional[str], end: Optional[str], days: int):
    """Returns (start, end, clamped) with end >= start and at most MAX_RANGE_DAYS days."""
    first = _parse_day(start, date.today())
    last = _parse_day(end, first + timedelta(days=max(days, 1) - 1))
    if last < first:
        first, last = last, first
    clamped = False
    if (last - first).days + 1 > MAX_RANGE_DAYS:
        last = first + timedelta(days=MAX_RANGE_DAYS - 1)
        clamped = True
    return first, last, clamped


def _chunks(first: date, last: date):
    cur = first
    while cur <= last:
        stop = min(cur + timedelta(days=CHUNK_DAYS - 1), last)
        yield cur, stop
        cur = stop + timedelta(days=1)


# ---------------------------------------------------------------- endpoints

# ---------------------------------------------------------------- network info / landing page

BIND = {"host": "127.0.0.1", "port": 8000}  # filled in at startup


def _settings_file() -> Path:
    override = os.environ.get("WEBUNTIS_SETTINGS_DIR")
    base = Path(override) if override else Path(os.environ.get("APPDATA") or Path.home() / ".config") / "WebUntisApp"
    return base / "settings.json"


def lan_enabled_in_settings() -> bool:
    """The GUI setting 'backend_lan' (default on) adds the active network address of this PC next to 127.0.0.1."""
    try:
        return bool(json.loads(_settings_file().read_text(encoding="utf-8")).get("backend_lan", True))
    except Exception:
        return True


def local_ips() -> List[str]:
    """IPv4 addresses of this computer (LAN), best guess first. No traffic is sent."""
    ips: List[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            ips.append(sock.getsockname()[0])
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return [ip for ip in ips if not ip.startswith("127.")]


@app.get("/api/info")
def info() -> Dict[str, Any]:
    hosts = BIND.get("hosts") or [BIND["host"]]
    lan = any(h not in ("127.0.0.1", "localhost") for h in hosts)
    port = BIND["port"]
    lan_hosts = [h for h in hosts if h not in ("127.0.0.1", "localhost")]
    if "0.0.0.0" in lan_hosts:
        lan_hosts = local_ips()
    return {"status": True, "bind_host": ", ".join(hosts), "port": port, "lan_enabled": lan,
            "local_url": f"http://127.0.0.1:{port}",
            "lan_urls": [f"http://{ip}:{port}" for ip in lan_hosts],
            "lan_ips": local_ips()}


@app.get("/", response_class=HTMLResponse)
def landing() -> str:
    return """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=./static/index.html">
<title>WebUntis Stundenplan</title></head>
<body><a href="./static/index.html">WebUntis Stundenplan oeffnen</a></body></html>"""


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "message": "WebUntis API v2 is running",
            "timestamp": datetime.now().isoformat()}


SCHOOL_SEARCH_URL = "https://mobile.webuntis.com/ms/schoolquery2"
DEMO_SCHOOL_ENTRY = {"displayName": "Demo (offline, no network needed)", "server": "demo.local",
                     "loginName": "demo", "address": "Built-in sample data, login Demo / Demo"}


@app.get("/api/schools/search")
def school_search(q: str = "") -> Dict[str, Any]:
    """Search schools via the public WebUntis school search (no login needed)."""
    q = q.strip()
    results: List[Dict[str, Any]] = []
    if not q or "demo" in q.lower():
        results.append(DEMO_SCHOOL_ENTRY)
    if len(q) < 2:
        return {"status": True, "data": results, "message": "Type at least 2 characters"}
    try:
        resp = requests.post(
            SCHOOL_SEARCH_URL,
            json={"id": "wu_schulsuche", "method": "searchSchool",
                  "params": [{"search": q}], "jsonrpc": "2.0"},
            headers={"User-Agent": USER_AGENT}, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(payload["error"].get("message", "school search error"))
        for sch in payload.get("result", {}).get("schools", [])[:50]:
            results.append({
                "displayName": sch.get("displayName") or sch.get("loginName", ""),
                "server": sch.get("server", ""),
                "loginName": sch.get("loginName", ""),
                "address": sch.get("address", ""),
            })
    except Exception as exc:
        log.warning("School search failed for %r: %s", q, exc)
        return {"status": bool(results), "data": results, "message": f"School search failed: {exc}"}
    return {"status": True, "data": results, "message": f"{len(results)} schools"}



@app.get("/api/schools/nearby")
def schools_nearby(lat: float, lon: float) -> Dict[str, Any]:
    """Schools near a position: reverse-geocode (OpenStreetMap Nominatim) to a town, then search."""
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 14, "addressdetails": 1},
            headers={"User-Agent": USER_AGENT}, timeout=10)
        resp.raise_for_status()
        addr = resp.json().get("address", {})
    except Exception as exc:
        log.warning("Reverse geocoding failed: %s", exc)
        return {"status": False, "data": [], "location": "", "message": f"Location lookup failed: {exc}"}
    town = (addr.get("city") or addr.get("town") or addr.get("village")
            or addr.get("municipality") or addr.get("county") or "")
    if not town:
        return {"status": False, "data": [], "location": "", "message": "No town found for this position"}
    result = school_search(town)
    result["location"] = town
    return result


@app.post("/api/auth/validate")
def login(req: LoginRequest) -> Dict[str, Any]:
    url = _server_url(req.server)
    school = _resolve_school_login(req.server, req.school)
    log.info("Login attempt: user=%s school=%s server=%s", req.username, school, url)
    try:
        if url.lower().split(":")[0] in DEMO_HOSTS:
            if (req.username, req.password) != ("Demo", "Demo"):
                raise ValueError("Offline demo mode: use Demo / Demo")
            log.info("Offline demo mode active")
            session = DemoSession()
        else:
            session = webuntis.Session(
                server=url, school=school, username=req.username,
                password=req.password, useragent=USER_AGENT,
            ).login()
    except Exception as exc:
        log.warning("Login failed for user=%s: %s", req.username, exc)
        return {"status": False, "access_token": None, "user_id": None,
                "user_name": None, "message": f"Login failed: {exc}"}

    token = secrets.token_urlsafe(32)
    SESSIONS[token] = {"session": session, "user": req.username,
                       "created": datetime.now(), "lock": threading.Lock()}
    login_result = getattr(session, "login_result", {}) or {}
    log.info("Login OK: user=%s personType=%s", req.username, login_result.get("personType"))
    return {"status": True, "access_token": token,
            "user_id": login_result.get("personId"), "user_name": req.username,
            "message": "Login successful"}


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    tok = _token(authorization)
    entry = SESSIONS.pop(tok, None) if tok else None
    if entry:
        try:
            entry["session"].logout(suppress_errors=True)
        except Exception:
            log.debug("logout error ignored", exc_info=True)
    return {"status": True, "message": "Logged out"}


@app.get("/api/users/my/timetable")
def my_timetable(start: Optional[str] = None, end: Optional[str] = None, days: int = 7,
                 authorization: Optional[str] = Header(default=None)):
    """Timetable for start..end (YYYY-MM-DD). Default: today + 7 days. Max 62 days per call."""
    first, last, clamped = _resolve_range(start, end, days)

    def run(s: webuntis.Session):
        # 1) REST API of the web client: teacher short names + full-day events (Wandertag)
        if getattr(s, "config", None) is not None and not isinstance(s, DemoSession):
            try:
                rest_rows: List[Dict[str, Any]] = []
                for a, b in _chunks(first, last):
                    rest_rows.extend(rest_timetable.fetch_rows(s, a, b))
                rest_rows.sort(key=lambda r: (r["date"], r["startTime"][-5:], r["endTime"]))
                return rest_rows, {"range": {"start": first.isoformat(), "end": last.isoformat(),
                                             "clamped": clamped}, "source": "rest"}
            except Exception:
                log.warning("REST timetable unavailable, falling back to JSON-RPC", exc_info=True)
        periods: List[Any] = []
        klasse = None
        for a, b in _chunks(first, last):
            try:
                if klasse is None:
                    periods.extend(s.my_timetable(start=a, end=b))
                else:
                    periods.extend(s.timetable(klasse=klasse, start=a, end=b))
            except Exception:
                log.info("my_timetable unavailable, falling back to first class", exc_info=True)
                klasse = s.klassen()[0]
                periods.extend(s.timetable(klasse=klasse, start=a, end=b))
        tmap = _teacher_map(s, first, last) if not isinstance(s, DemoSession) else {}
        rows = [_period_row(p, tmap) for p in sorted(periods, key=lambda p: p.start)]
        return rows, {"range": {"start": first.isoformat(), "end": last.isoformat(), "clamped": clamped}}
    return _guarded(authorization, "Timetable", run)


@app.get("/api/schools/{school_id}/classes")
def classes(school_id: int, authorization: Optional[str] = Header(default=None)):
    def run(s: webuntis.Session) -> List[Any]:
        rows = []
        for k in s.klassen():
            teacher = getattr(k, "teacher1", None)
            rows.append({"id": int(k.id), "name": k.name,
                         "teacher": getattr(teacher, "name", None) if teacher else None})
        return rows
    return _guarded(authorization, "Classes", run)


@app.get("/api/schools/{school_id}/timetable")
def school_timetable(school_id: int, class_id: Optional[int] = None,
                     start: Optional[str] = None, end: Optional[str] = None, days: int = 1,
                     authorization: Optional[str] = Header(default=None)):
    first, last, clamped = _resolve_range(start, end, days)

    def run(s: webuntis.Session):
        klasse = class_id if class_id is not None else s.klassen()[0]
        periods: List[Any] = []
        for a, b in _chunks(first, last):
            periods.extend(s.timetable(klasse=klasse, start=a, end=b))
        rows = [_period_row(p) for p in sorted(periods, key=lambda p: p.start)]
        return rows, {"range": {"start": first.isoformat(), "end": last.isoformat(), "clamped": clamped}}
    return _guarded(authorization, "School timetable", run)


@app.get("/api/data/teachers")
def teachers(authorization: Optional[str] = Header(default=None)):
    def run(s: webuntis.Session) -> List[Any]:
        try:
            return [{"id": int(t.id), "name": t.name,
                     "shortName": t.name,
                     "fullName": getattr(t, "full_name", None) or getattr(t, "long_name", t.name)}
                    for t in s.teachers()]
        except Exception:
            first = date.today() - timedelta(days=date.today().weekday())
            rows: Dict[str, Dict[str, Any]] = {}
            for a, b in _chunks(first, first + timedelta(days=13)):
                for row in rest_timetable.fetch_rows(s, a, b):
                    for short in [x.strip() for x in str(row.get("teacher") or "").split(",") if x.strip()]:
                        rows.setdefault(short, {"id": None, "name": short, "shortName": short, "fullName": short})
            return sorted(rows.values(), key=lambda item: item["shortName"])
    return _guarded(authorization, "Teachers", run)


@app.get("/api/data/teachers/{short_name}/lessons")
def teacher_lessons(short_name: str, authorization: Optional[str] = Header(default=None)):
    def run(s: webuntis.Session) -> List[Any]:
        first = date.today() - timedelta(days=date.today().weekday())
        rows: List[Dict[str, Any]] = []
        for a, b in _chunks(first, first + timedelta(days=13)):
            rows.extend(rest_timetable.fetch_rows(s, a, b))
        found = []
        for row in rows:
            teachers = [x.strip().lower() for x in str(row.get("teacher") or "").split(",")]
            if short_name.strip().lower() in teachers and not row.get("allDay"):
                found.append(row)
        unique: Dict[str, Dict[str, Any]] = {}
        for row in found:
            key = f"{row.get('date')}|{str(row.get('startTime'))[-5:]}|{row.get('subject')}"
            if key not in unique or unique[key].get("cancelled"):
                unique[key] = row
        return sorted(unique.values(), key=lambda r: (r.get("date") or "", str(r.get("startTime") or "")[-5:]))
    return _guarded(authorization, "Teacher lessons", run)


@app.get("/api/data/substitutions")
def substitutions(days: int = 14, authorization: Optional[str] = Header(default=None)):
    def run(s: webuntis.Session) -> List[Any]:
        start = date.today()
        end = start + timedelta(days=days)
        rows = []
        for sub in s.substitutions(start=start, end=end):
            rows.append({
                "date": sub.start.date().isoformat(),
                "lesson": int(sub.start.hour * 100 + sub.start.minute),
                "originalTeacher": _names(getattr(sub, "original_teachers", [])),
                "substituteTeacher": _names(sub.teachers),
                "subject": _names(sub.subjects),
            })
        return rows
    return _guarded(authorization, "Substitutions", run)


@app.get("/api/data/holidays")
def holidays(authorization: Optional[str] = Header(default=None)):
    def run(s: webuntis.Session) -> List[Any]:
        def iso(v: Any) -> str:
            return v.isoformat() if hasattr(v, "isoformat") else str(v)
        return [{"startDate": iso(h.start), "endDate": iso(h.end), "name": h.name,
                 "isLongHoliday": (h.end - h.start).days > 14} for h in s.holidays()]
    return _guarded(authorization, "Holidays", run)


@app.get("/api/data/exams")
def exams_ep(authorization: Optional[str] = Header(default=None)):
    return _guarded(authorization, "Exams", lambda s: extras.collect("exams", s))


@app.get("/api/data/homework")
def homework_ep(authorization: Optional[str] = Header(default=None)):
    return _guarded(authorization, "Homework", lambda s: extras.collect("homework", s))


@app.get("/api/data/messages")
def messages_ep(authorization: Optional[str] = Header(default=None)):
    """Messages of the day (HTML scraping, see extras.py)"""
    return _guarded(authorization, "Messages", lambda s: extras.collect("messages_day", s))


@app.get("/api/data/inbox")
def inbox_ep(authorization: Optional[str] = Header(default=None)):
    return _guarded(authorization, "Inbox", lambda s: extras.collect("inbox", s))


@app.get("/api/data/features")
def features_ep(authorization: Optional[str] = Header(default=None)):
    """Which optional areas does this school offer? (dashboard tiles)"""
    return _guarded(authorization, "Features", lambda s: extras.features(s))


@app.get("/api/data/absences")
def absences(authorization: Optional[str] = Header(default=None)):
    # The JSON-RPC API used here has no absence endpoint; keep the route for the clients.
    if not _get_entry(authorization):
        return _fail("Not logged in or session expired")
    return {"status": True, "data": [], "message": "Absences are not available via this API"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("BACKEND_PORT", "8000"))
    override = os.getenv("BACKEND_HOST")
    if override:
        hosts = [override]  # e.g. 0.0.0.0 for every interface
    else:
        hosts = ["127.0.0.1"]
        if lan_enabled_in_settings():
            active = local_ips()[:1]  # address of the network interface this PC is using right now
            hosts += active
    BIND.update(host=hosts[0], hosts=hosts, port=port)
    log.info("Starting WebUntis API v2 on %s:%s", ", ".join(hosts), port)
    sockets = []
    for h in hosts:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((h, port))
        except OSError as exc:
            log.warning("Could not listen on %s:%s (%s)", h, port, exc)
            sock.close()
            continue
        sockets.append(sock)
    if not sockets:
        raise SystemExit(f"Port {port} is not available on any address")
    server = uvicorn.Server(uvicorn.Config(app, log_level="debug"))
    server.run(sockets=sockets)
