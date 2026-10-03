"""
Extra WebUntis data sources for the dashboard (exams, homework, messages of the day, inbox).

Sources (checked against the two reference projects):
  * JonasJoKuJonas/homeassistant-WebUntis  -> GET /WebUntis/api/exams, GET /WebUntis/api/homeworks/lessons
  * Naumsede/webuntis-home-assistant       -> "messages of the day" are only embedded in the HTML of
                                              /WebUntis/main.do (MessageOfDayList widget)
  * inbox ("Mitteilungen" in the new UI)   -> no public documentation; candidate REST paths are probed
                                              and the first answer is logged (UNVERIFIED).

Every collector returns (rows, extra). `extra["available"]` is False when the school does not offer or
does not release the area; the GUI then shows "not used by this school" instead of an error.
"""
import json
import logging
import re
from datetime import date, timedelta
from html import unescape
from typing import Any, Dict, List, Tuple

import requests

import rest_timetable as rt

log = logging.getLogger("webuntis_extras")

NOT_USED = "Von dieser Schule nicht unterstützt"
_logged_samples: set = set()


class Unavailable(Exception):
    """The school does not offer this area (or the account may not see it)."""


def _config(session: Any) -> Dict[str, Any]:
    config = getattr(session, "config", None)
    if config is None:
        raise Unavailable("offline demo mode")
    return config


def _auth_headers(config: Any, extra: Dict[str, str] = None) -> Dict[str, str]:
    """Bearer token + tenant id like the WebUntis web client sends (cookie-only calls get HTTP 403 here)."""
    headers = {"Accept": "application/json"}
    try:
        token = rt._token(config)
        headers["Authorization"] = "Bearer " + token
        tenant = rt._tenant(config, token)
        if tenant:
            headers["tenant-id"] = tenant
    except Exception as exc:  # token is optional for the classic endpoints
        log.info("no REST token for extras: %s", exc)
    headers.update(extra or {})
    return headers


def _get(session: Any, path: str, params: Dict[str, Any] = None, headers: Dict[str, str] = None):
    config = _config(session)
    resp = rt._http(config).get(rt._base(config) + path, params=params,
                                headers=headers or _auth_headers(config),
                                cookies=rt._cookies(config), timeout=25)
    if resp.status_code in (401, 403, 404, 405):
        raise Unavailable(f"HTTP {resp.status_code} for {path}")
    resp.raise_for_status()
    return resp


def _sample(kind: str, text: str) -> None:
    if kind not in _logged_samples:
        _logged_samples.add(kind)
        log.info("%s sample (first 1500 chars): %s", kind, text[:1500])


def _ymd(value: Any) -> str:
    s = str(value or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s[:10]


def _hhmm(value: Any) -> str:
    try:
        n = int(value)
        return f"{n // 100:02d}:{n % 100:02d}"
    except (TypeError, ValueError):
        return str(value or "")


def _person_id(session: Any) -> Any:
    return (getattr(session, "login_result", {}) or {}).get("personId")


# ---------------------------------------------------------------- exams

def _exams_rest(session: Any) -> List[Dict[str, Any]]:
    """Fallback: new REST view API (response format still to be confirmed from the logged sample)."""
    today = date.today()
    me = _person_id(session)
    params = {"startDate": (today - timedelta(days=30)).isoformat(),
              "endDate": (today + timedelta(days=240)).isoformat(), "studentId": me}
    last = "no candidate answered"
    for path in ("/api/rest/view/v1/exams", "/api/rest/view/v1/exams/list"):
        try:
            resp = _get(session, path, params)
        except Unavailable as exc:
            last = str(exc)
            continue
        _sample("exams-rest " + path, resp.text)
        payload = resp.json()
        items = payload if isinstance(payload, list) else next(
            (payload[k] for k in ("exams", "data", "items", "entries") if isinstance(payload.get(k), list)), [])
        rows = []
        for e in items:
            rows.append({"id": e.get("id"), "date": _ymd(e.get("examDate") or e.get("date") or e.get("start")),
                         "startTime": _hhmm(e.get("startTime")), "endTime": _hhmm(e.get("endTime")),
                         "subject": _txt(e.get("subject")), "name": e.get("name") or e.get("title") or "",
                         "type": _txt(e.get("examType") or e.get("type")), "text": e.get("text") or "",
                         "teachers": _txt(e.get("teachers")), "rooms": _txt(e.get("rooms"))})
        rows.sort(key=lambda r: (r["date"], r["startTime"]))
        return rows
    raise Unavailable(last)


def _txt(v: Any) -> str:
    if isinstance(v, dict):
        return str(v.get("shortName") or v.get("name") or v.get("displayName") or "")
    if isinstance(v, list):
        return ", ".join(_txt(x) for x in v)
    return "" if v is None else str(v)


def exams(session: Any) -> List[Dict[str, Any]]:
    try:
        return _exams_classic(session)
    except Unavailable:
        return _exams_rest(session)


def _exams_classic(session: Any) -> List[Dict[str, Any]]:
    today = date.today()
    resp = _get(session, "/api/exams", {"startDate": (today - timedelta(days=30)).strftime("%Y%m%d"),
                                         "endDate": (today + timedelta(days=240)).strftime("%Y%m%d")})
    _sample("exams", resp.text)
    items = ((resp.json().get("data") or {}).get("exams")) or []
    me = _person_id(session)
    rows = []
    for e in items:
        students = [s.get("id") for s in (e.get("assignedStudents") or [])]
        if students and me is not None and me not in students:
            continue
        rows.append({
            "id": e.get("id"), "date": _ymd(e.get("examDate")),
            "startTime": _hhmm(e.get("startTime")), "endTime": _hhmm(e.get("endTime")),
            "subject": e.get("subject") or "", "name": e.get("name") or "",
            "type": e.get("examType") or "", "text": e.get("text") or "",
            "teachers": ", ".join(e.get("teachers") or []), "rooms": ", ".join(e.get("rooms") or []),
        })
    rows.sort(key=lambda r: (r["date"], r["startTime"]))
    return rows


# ---------------------------------------------------------------- homework

def homework(session: Any) -> List[Dict[str, Any]]:
    try:
        return _homework_classic(session)
    except Unavailable:
        return _homework_rest(session)


def _homework_rest(session: Any) -> List[Dict[str, Any]]:
    """Fallback: new REST view API (response format still to be confirmed from the logged sample)."""
    today = date.today()
    params = {"startDate": (today - timedelta(days=14)).isoformat(),
              "endDate": (today + timedelta(days=60)).isoformat()}
    last = "no candidate answered"
    for path in ("/api/rest/view/v1/homeworks", "/api/rest/view/v1/homeworks/lessons"):
        try:
            resp = _get(session, path, params)
        except Unavailable as exc:
            last = str(exc)
            continue
        _sample("homework-rest " + path, resp.text)
        payload = resp.json()
        items = payload if isinstance(payload, list) else next(
            (payload[k] for k in ("homeworks", "data", "items", "entries") if isinstance(payload.get(k), list)), [])
        rows = []
        for h in items:
            due, assigned = _ymd(h.get("dueDate")), _ymd(h.get("date") or h.get("assignedDate"))
            done = bool(h.get("completed"))
            group = "completed" if done else "overdue" if due and due < today.isoformat() else \
                "due_soon" if due and due <= (today + timedelta(days=3)).isoformat() else "open"
            rows.append({"id": h.get("id"), "subject": _txt(h.get("subject")), "teacher": _txt(h.get("teacher")),
                         "assigned": assigned, "due": due, "text": h.get("text") or h.get("description") or "",
                         "completed": done, "group": group})
        rows.sort(key=lambda r: (r["due"] or "9999"))
        return rows
    raise Unavailable(last)


def _homework_classic(session: Any) -> List[Dict[str, Any]]:
    today = date.today()
    resp = _get(session, "/api/homeworks/lessons", {"startDate": (today - timedelta(days=14)).strftime("%Y%m%d"),
                                                    "endDate": (today + timedelta(days=60)).strftime("%Y%m%d")})
    _sample("homework", resp.text)
    data = resp.json().get("data") or {}
    lessons = {l.get("id"): l for l in data.get("lessons") or []}
    teachers = {t.get("id"): t for t in data.get("teachers") or []}
    records = data.get("records") or []
    rows = []
    for hw in data.get("homeworks") or []:
        record = next((r for r in records if r.get("homeworkId") == hw.get("id")), {})
        due, assigned = _ymd(hw.get("dueDate")), _ymd(hw.get("date"))
        completed = bool(hw.get("completed"))
        if completed:
            group = "completed"
        elif due and due < today.isoformat():
            group = "overdue"
        elif due and due <= (today + timedelta(days=3)).isoformat():
            group = "due_soon"
        else:
            group = "open"
        rows.append({
            "id": hw.get("id"), "subject": (lessons.get(hw.get("lessonId")) or {}).get("subject", ""),
            "teacher": (teachers.get(record.get("teacherId")) or {}).get("name", ""),
            "assigned": assigned, "due": due, "text": hw.get("text") or "",
            "completed": completed, "group": group,
        })
    rows.sort(key=lambda r: (r["due"] or "9999"))
    return rows


# ---------------------------------------------------------------- messages of the day

def messages_of_day(session: Any) -> List[Dict[str, Any]]:
    resp = _get(session, "/main.do", {"request.preventCache": "1"}, {"Accept": "text/html"})
    match = re.search(r'data-dojo-type="grupet/widget/app/MessageOfDayList"\s+data-dojo-props="([^"]*(?:&#034;[^"]*)*)"',
                      resp.text)
    if not match:
        raise Unavailable("MessageOfDayList not found (new UI or no messages widget)")
    props = unescape(match.group(1))
    found = re.search(r'"messagesOfDay"\s*:\s*(\[.*?\])\s*,\s*"editable"', props, re.DOTALL)
    if not found:
        raise Unavailable("messagesOfDay not found")
    return [{"subject": m.get("subject", ""), "text": m.get("body", ""), "sender": "", "date": ""}
            for m in json.loads(found.group(1))]


# ---------------------------------------------------------------- inbox (verified 2026-10-03: GET /api/rest/view/v1/messages)

INBOX_CANDIDATES = ["/api/rest/view/v1/messages", "/api/rest/view/v1/messages/inbox"]


def inbox(session: Any) -> List[Dict[str, Any]]:
    config = _config(session)
    headers = _auth_headers(config)
    last_error = "no candidate answered"
    for path in INBOX_CANDIDATES:
        try:
            resp = _get(session, path, None, headers)
        except Unavailable as exc:
            last_error = str(exc)
            continue
        _sample("inbox " + path, resp.text)
        payload = resp.json()
        items = payload if isinstance(payload, list) else next(
            (payload[k] for k in ("messages", "incomingMessages", "data", "items") if isinstance(payload.get(k), list)), [])
        rows = []
        for m in items:
            sender = m.get("sender")
            if isinstance(sender, dict):
                sender = sender.get("displayName") or sender.get("name")
            rows.append({"id": m.get("id"), "read": m.get("isMessageRead"),
                         "subject": m.get("subject") or m.get("title") or "",
                         "sender": sender or "", "date": str(m.get("sentDateTime") or m.get("date") or "")[:16],
                         "text": m.get("contentPreview") or m.get("body") or m.get("text") or ""})
        return rows
    raise Unavailable(last_error)


COLLECTORS = {"exams": exams, "homework": homework, "messages_day": messages_of_day, "inbox": inbox}


def collect(kind: str, session: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    try:
        rows = COLLECTORS[kind](session)
        return rows, {"available": True, "note": "" if rows else "Keine Einträge"}
    except Unavailable as exc:
        log.info("%s not available: %s", kind, exc)
        return [], {"available": False, "note": NOT_USED, "detail": str(exc)}


def features(session: Any) -> List[Dict[str, Any]]:
    out = []
    for kind in COLLECTORS:
        try:
            rows, extra = collect(kind, session)
        except (requests.RequestException, ValueError) as exc:
            rows, extra = [], {"available": False, "note": NOT_USED, "detail": str(exc)}
        out.append({"id": kind, "available": extra["available"], "count": len(rows),
                    "note": extra["note"], "detail": extra.get("detail", "")})
    return out
