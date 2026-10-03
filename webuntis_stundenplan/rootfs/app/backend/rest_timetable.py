"""
Timetable via the REST API of the WebUntis web client (the same one the browser UI and the
Untis app use). Unlike the old JSON-RPC getTimetable it returns teacher short names for
students and full-day events such as "Wandertag".

fetch_rows() returns rows in the same shape as _period_row() in webuntis_api_v2.py, plus:
  allDay (bool), title (event/lesson text), type, status
It raises on any problem; the caller falls back to the JSON-RPC path.
"""
import base64
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("webuntis_rest")

RESOURCE_TYPES = {1: "CLASS", 2: "TEACHER", 3: "SUBJECT", 4: "ROOM", 5: "STUDENT"}
TOKEN_TTL = 8 * 60
_sample_logged = False


_STATE: Dict[str, Dict[str, Any]] = {}  # per-session cache (webuntis' FilterDict has no .get / free keys)


def _cfg(config: Any, key: str, default: Any = None) -> Any:
    try:
        return config[key]
    except (KeyError, TypeError):
        return default


def _st(config: Any) -> Dict[str, Any]:
    return _STATE.setdefault(str(_cfg(config, "jsessionid", "")), {})


def _base(config: Dict[str, Any]) -> str:
    server = str(config["server"])  # e.g. https://host/WebUntis/jsonrpc.do
    idx = server.find("/WebUntis")
    return server[:idx + len("/WebUntis")] if idx >= 0 else server.rstrip("/") + "/WebUntis"


def _cookies(config: Dict[str, Any]) -> Dict[str, str]:
    school = str(_cfg(config, "school", ""))
    return {
        "JSESSIONID": str(config["jsessionid"]),
        "schoolname": '"_' + base64.b64encode(school.encode("utf-8")).decode("ascii") + '"',
    }


def _http(config: Dict[str, Any]) -> requests.Session:
    http = _st(config).get("http")
    if http is None:
        http = requests.Session()
        http.headers["User-Agent"] = str(_cfg(config, "useragent", "webuntis-app"))
        _st(config)["http"] = http
    return http


def _token(config: Dict[str, Any]) -> str:
    cached = _st(config).get("token")
    if cached and time.time() - cached[1] < TOKEN_TTL:
        return cached[0]
    resp = _http(config).get(_base(config) + "/api/token/new", cookies=_cookies(config), timeout=20)
    resp.raise_for_status()
    token = resp.text.strip().strip('"')
    if not token or token.startswith("<"):
        raise RuntimeError("no REST token received")
    _st(config)["token"] = (token, time.time())
    return token


def _tenant(config: Dict[str, Any], token: str) -> str:
    cached = _st(config).get("tenant")
    if cached:
        return cached
    resp = _http(config).get(_base(config) + "/api/rest/view/v1/app/data",
                             headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                             cookies=_cookies(config), timeout=20)
    resp.raise_for_status()
    tenant = str((resp.json().get("tenant") or {}).get("id") or "")
    if tenant:
        _st(config)["tenant"] = tenant
    return tenant


def _name(item: Dict[str, Any]) -> str:
    cur = item.get("current") if isinstance(item.get("current"), dict) else item
    if not isinstance(cur, dict):
        return ""
    return str(cur.get("shortName") or cur.get("displayName") or cur.get("longName") or cur.get("name") or "")


def _kind(item: Dict[str, Any], fallback: str) -> str:
    cur = item.get("current") if isinstance(item.get("current"), dict) else item
    return str((cur or {}).get("type") or fallback).upper()


def _hm(iso: str) -> str:
    return iso[11:16] if len(iso) >= 16 else ""


def _entry_to_row(day: str, e: Dict[str, Any], all_day: bool = False) -> Optional[Dict[str, Any]]:
    dur = e.get("duration") or {}
    start, end = str(dur.get("start", "")), str(dur.get("end", ""))
    if all_day and not start:
        start, end = f"{day}T00:00", f"{day}T23:59"
    if len(start) < 16:
        return None
    teachers, subjects, rooms = [], [], []
    for pos, fallback in (("position1", "TEACHER"), ("position2", "SUBJECT"), ("position3", "ROOM")):
        for item in e.get(pos) or []:
            if not isinstance(item, dict):
                continue
            name = _name(item)
            if not name:
                continue
            kind = _kind(item, fallback)
            target = {"TEACHER": teachers, "SUBJECT": subjects, "ROOM": rooms}.get(kind)
            if target is None:
                target = {"position1": teachers, "position2": subjects, "position3": rooms}[pos]
            if name not in target:
                target.append(name)
    status = str(e.get("status") or "").upper()
    etype = str(e.get("type") or "").upper()
    title = str(e.get("name") or e.get("substitutionText") or e.get("lessonText") or e.get("notesAll") or "").strip()
    cancelled = status in ("CANCELLED", "CANCELED", "CANCEL")
    substitution = status in ("SUBSTITUTION", "CHANGED", "ADDITIONAL", "ROOMSUBSTITUTION", "TEACHERSUBSTITUTION")
    dt = datetime.strptime(start[:16], "%Y-%m-%dT%H:%M")
    return {
        "id": int((e.get("ids") or [0])[0] or 0),
        "date": start[:10],
        "startTime": f"{dt:%a %d.%m. %H:%M}",
        "endTime": _hm(end),
        "subject": ", ".join(subjects),
        "teacher": ", ".join(teachers),
        "classroom": ", ".join(rooms),
        "substitution": "irregular" if substitution else None,
        "cancelled": cancelled,
        "exam": etype == "EXAM" or status == "EXAM",
        "allDay": bool(all_day or (etype == "EVENT" and not subjects and not teachers)),
        "title": title,
        "type": etype,
        "status": status,
    }


def fetch_rows(session: Any, first, last) -> List[Dict[str, Any]]:
    global _sample_logged
    config = session.config
    login = getattr(session, "login_result", {}) or {}
    person_id = login.get("personId")
    rtype = RESOURCE_TYPES.get(int(login.get("personType") or 5), "STUDENT")
    if not person_id:
        raise RuntimeError("no personId from login")
    token = _token(config)
    tenant = _tenant(config, token)
    params = {"start": first.isoformat(), "end": last.isoformat(), "format": "2",
              "resourceType": rtype, "resources": str(person_id), "periodTypes": "",
              "timetableType": "MY_TIMETABLE", "layout": "START_TIME"}
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if tenant:
        headers["tenant-id"] = tenant
    resp = _http(config).get(_base(config) + "/api/rest/view/v1/timetable/entries",
                             params=params, headers=headers, cookies=_cookies(config), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not _sample_logged:
        _sample_logged = True
        log.info("REST timetable sample (first 3000 chars): %s", resp.text[:3000])
    rows: List[Dict[str, Any]] = []
    for day in data.get("days") or []:
        day_iso = str(day.get("date", ""))[:10]
        for e in day.get("gridEntries") or []:
            row = _entry_to_row(day_iso, e)
            if row:
                rows.append(row)
        for e in day.get("dayEntries") or []:
            row = _entry_to_row(day_iso, e, all_day=True)
            if row:
                row["title"] = row["title"] or str(e.get("name") or e.get("title") or "")
                rows.append(row)
    return rows
