"""
tools/event_tools.py
--------------------
Tools for content_related_issue -> event_related_issue: event video missing,
event video not playing, and event progress not updating.

Built from the EVENT_RELATED_ISSUES API workflow (use cases 1-3; use case 4,
event certificate not generated, is out of scope for this leaf).

Endpoints (IGOT_API_HOST_URL = https://portal.uat.karmayogibharat.net in UAT):
  POST /api/private/user/v1/search                — email -> user_id
  GET  /api/user/private/v1/events/list/{user_id}  — enrolled events (picker + progress)
  GET  /api/event/v4/read/{event_id}               — event config (registrationLink)
  GET  {GOOGLE_YOUTUBE_API_BASE_URL}/videos (from env)   — real video duration (use case 1)

The branch decisions (duration < 60s, embed-URL validity, time spent vs 600s,
already-complete) are computed here, deterministically, rather than left to the
LLM — matching enrolment_tools.py / course_progress_tools.py.
"""

import json
import logging
import re
from urllib.parse import parse_qs, urlparse

import requests
from langchain.tools import tool

from app.core.utils.config import IGOT_API_HOST_URL, IGOT_KEY, GOOGLE_YOUTUBE_API_KEY, GOOGLE_YOUTUBE_API_BASE_URL

logger = logging.getLogger(__name__)

_HEADERS_JSON = {
    "Authorization": f"Bearer {IGOT_KEY}",
    "Content-Type": "application/json",
}

USER_NOT_FOUND_MESSAGE = "User profile not found."

_MIN_VIDEO_SECONDS = 60.0
_MIN_TIME_SPENT_SECONDS = 600.0

_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?)?$"
)


# ── Internal helpers ────────────────────────────────────────────────────────────

def _fetch_user_id(email: str) -> str | None:
    """POST /api/private/user/v1/search by email -> user_id."""
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    resp = requests.post(url, json={"request": {"filters": {"email": email}}}, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    content = resp.json().get("result", {}).get("response", {}).get("content", [])
    return content[0].get("id") if content else None


def _fetch_events(user_id: str) -> list:
    """GET events/list/{user_id} -> result.events[]."""
    url = f"{IGOT_API_HOST_URL}/api/user/private/v1/events/list/{user_id}"
    resp = requests.get(url, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", {}).get("events", []) or []


def _fetch_event_config(event_id: str) -> dict:
    """GET event/v4/read/{event_id} -> the event object (envelope-tolerant)."""
    url = f"{IGOT_API_HOST_URL}/api/event/v4/read/{event_id}"
    resp = requests.get(url, headers=_HEADERS_JSON, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    data = data.get("result", data) if isinstance(data, dict) else {}
    return data.get("event", {}) or {}


def _parse_json_field(value) -> dict:
    """A progress-details field may arrive as a JSON string, a dict, or be empty."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _extract_time_spent_seconds(event_item: dict) -> float | None:
    """lrcProgressDetails.duration, falling back to userEventConsumption[0].progressdetails.duration."""
    details = _parse_json_field(event_item.get("lrcProgressDetails"))
    if not details:
        consumption = event_item.get("userEventConsumption") or []
        if consumption and isinstance(consumption[0], dict):
            details = _parse_json_field(consumption[0].get("progressdetails"))
    duration = details.get("duration")
    try:
        return float(duration) if duration is not None else None
    except (TypeError, ValueError):
        return None


def _has_issued_certificates(event_item: dict) -> bool:
    return bool(event_item.get("issuedCertificates"))


def _is_youtube_embed_url(url: str | None) -> bool:
    return bool(url) and re.search(r"youtube\.com/embed/", url) is not None


def _extract_youtube_video_id(url: str | None) -> str | None:
    """Pull the 11-char video ID from an embed / watch / youtu.be URL."""
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    candidate = None
    if "youtu.be" in host:
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif "youtube.com" in host or "youtube-nocookie.com" in host:
        if parsed.path.startswith("/embed/"):
            candidate = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else None
        else:
            candidate = (parse_qs(parsed.query).get("v") or [None])[0]
    return candidate if candidate and _YT_ID_RE.match(candidate) else None


def _iso8601_duration_to_seconds(value: str | None) -> float | None:
    """'PT45S' -> 45.0, 'PT1H2M3S' -> 3723.0. None if unparseable."""
    if not value:
        return None
    m = _ISO_DURATION_RE.match(value)
    if not m:
        return None
    parts = {k: float(v) for k, v in m.groupdict().items() if v}
    return parts.get("d", 0) * 86400 + parts.get("h", 0) * 3600 + parts.get("m", 0) * 60 + parts.get("s", 0)


def _fetch_youtube_duration_seconds(video_id: str) -> float | None:
    """YouTube Data API v3 contentDetails.duration -> seconds. None if not found."""
    if not GOOGLE_YOUTUBE_API_BASE_URL or not GOOGLE_YOUTUBE_API_KEY:
        logger.warning("[event_tools] GOOGLE_YOUTUBE_API_BASE_URL / GOOGLE_YOUTUBE_API_KEY not configured")
        return None
    resp = requests.get(
        f"{GOOGLE_YOUTUBE_API_BASE_URL.rstrip('/')}/videos",
        params={"part": "contentDetails", "id": video_id, "key": GOOGLE_YOUTUBE_API_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    items = resp.json().get("items") or []
    if not items:
        return None
    return _iso8601_duration_to_seconds((items[0].get("contentDetails") or {}).get("duration"))


_STOPWORDS = {"the", "a", "an", "of", "on", "in", "for", "to", "and", "under", "session", "event", "video", "issue"}


def _name_tokens(text: str | None) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOPWORDS}


def _match_events_by_name(events: list, ticket_text: str) -> list:
    """Deterministically match an event name (or the whole ticket text, which may
    contain the name) against the enrolled events.

    Returns the best-scoring events only: an exact / contained name wins outright;
    otherwise events sharing at least 60% of their (non-stopword) name tokens with
    the text are ranked by that overlap. Empty list when nothing matches.
    """
    text = (ticket_text or "").strip().lower()
    if not text:
        return []
    text_tokens = _name_tokens(text)
    scored = []
    for e in events:
        name = (e.get("event_name") or "").strip().lower()
        if not name:
            continue
        if name in text or text in name:
            scored.append((2.0, e))
            continue
        name_tokens = _name_tokens(name)
        if not name_tokens:
            continue
        overlap = len(name_tokens & text_tokens) / len(name_tokens)
        if overlap >= 0.6:
            scored.append((overlap, e))
    if not scored:
        return []
    best = max(score for score, _ in scored)
    return [e for score, e in scored if score == best]


def _find_event(events: list, event_id: str) -> dict | None:
    return next((e for e in events if e.get("contentId") == event_id), None)


# ── STEP 0: enrolled event picker ───────────────────────────────────────────────

@tool
def get_user_events(email: str, event_name: str = "") -> str:
    """Fetch the user's enrolled events and match the ticket's event name.

    Pass event_name = the event name as written in the ticket (may be partial, or
    even the whole ticket subject — matching is done here, not by you).

    Returns found, count, and:
      matches: the events matching event_name (empty if none / no name given)
      events:  the full list [{event_id, event_name, completion_pct, certificate_issued}]
               (only when event_name is empty)
    Pass a matched event_id into the diagnosis tools next — never guess an event_id.
    """
    try:
        user_id = _fetch_user_id(email)
        if not user_id:
            return json.dumps({"found": False, "message": USER_NOT_FOUND_MESSAGE})

        events = _fetch_events(user_id)
        if not events:
            return json.dumps({"found": False, "message": "No enrolled events found for this user."})

        results = [
            {
                "event_id": e.get("contentId"),
                "event_name": (e.get("event") or {}).get("name"),
                "completion_pct": e.get("completionPercentage"),
                "certificate_issued": _has_issued_certificates(e),
            }
            for e in events
        ]
        if event_name.strip():
            matches = _match_events_by_name(results, event_name)
            return json.dumps({"found": True, "count": len(results), "event_name_given": True, "matches": matches})
        return json.dumps({"found": True, "count": len(results), "event_name_given": False, "events": results})
    except Exception as e:
        logger.error(f"[event_tools] get_user_events error: {e}")
        return json.dumps({"found": False, "error": str(e)})


# ── Use case 1: event video missing ─────────────────────────────────────────────

@tool
def check_event_video_duration(event_id: str) -> str:
    """Check whether an event has a real video by reading the duration of the
    YouTube video linked from its registrationLink.

    Returns status:
      'video_missing'   - duration is under 1 minute (content configuration issue)
      'video_valid'     - duration is 1 minute or more
      'unverifiable'    - no YouTube video ID could be parsed from registrationLink,
                          or the YouTube lookup failed / returned nothing
    Includes video_duration_seconds when known.
    """
    try:
        event = _fetch_event_config(event_id)
        video_id = _extract_youtube_video_id(event.get("registrationLink"))
        if not video_id:
            return json.dumps({"status": "unverifiable", "reason": "no_youtube_video_id"})

        duration = _fetch_youtube_duration_seconds(video_id)
        if duration is None:
            return json.dumps({"status": "unverifiable", "reason": "youtube_lookup_empty", "video_id": video_id})

        status = "video_missing" if duration < _MIN_VIDEO_SECONDS else "video_valid"
        return json.dumps({"status": status, "video_duration_seconds": duration, "video_id": video_id})
    except Exception as e:
        logger.error(f"[event_tools] check_event_video_duration error: {e}")
        return json.dumps({"status": "unverifiable", "reason": "error", "error": str(e)})


# ── Use case 2: event video not playing ─────────────────────────────────────────

@tool
def check_event_video_config(event_id: str) -> str:
    """Validate an event's video URL configuration (registrationLink).

    Returns status:
      'link_missing'  - registrationLink absent (configuration issue)
      'link_invalid'  - registrationLink is not a youtube.com/embed/ URL (configuration issue)
      'config_valid'  - valid embedded YouTube URL; give the user troubleshooting steps
      'error'         - the event could not be read
    """
    try:
        event = _fetch_event_config(event_id)
        link = event.get("registrationLink")
        if not link:
            return json.dumps({"status": "link_missing"})
        if not _is_youtube_embed_url(link):
            return json.dumps({"status": "link_invalid", "registration_link": link})
        return json.dumps({"status": "config_valid", "registration_link": link})
    except Exception as e:
        logger.error(f"[event_tools] check_event_video_config error: {e}")
        return json.dumps({"status": "error", "error": str(e)})


# ── Use case 3: event progress not updating ─────────────────────────────────────

@tool
def diagnose_event_progress(event_id: str, email: str) -> str:
    """Diagnose 'event progress not updating' for one enrolled event.

    Returns status:
      'already_complete' - certificate issued or completion >= 100%
      'in_progress'      - time spent missing or <= 600 seconds
      'technical_issue'  - time spent > 600 seconds but not complete (escalate)
      'not_enrolled'     - event_id not in the user's enrolled events
      'error'            - lookup failed
    Includes time_spent_seconds, completion_percentage, certificate_issued.
    """
    try:
        user_id = _fetch_user_id(email)
        if not user_id:
            return json.dumps({"status": "error", "message": USER_NOT_FOUND_MESSAGE})

        item = _find_event(_fetch_events(user_id), event_id)
        if not item:
            return json.dumps({"status": "not_enrolled"})

        time_spent = _extract_time_spent_seconds(item)
        certificate_issued = _has_issued_certificates(item)
        try:
            completion = float(item.get("completionPercentage") or 0)
        except (TypeError, ValueError):
            completion = 0.0

        if certificate_issued or completion >= 100.0:
            status = "already_complete"
        elif time_spent is None or time_spent <= _MIN_TIME_SPENT_SECONDS:
            status = "in_progress"
        else:
            status = "technical_issue"

        return json.dumps({
            "status": status,
            "event_name": (item.get("event") or {}).get("name"),
            "time_spent_seconds": time_spent,
            "completion_percentage": completion,
            "certificate_issued": certificate_issued,
        })
    except Exception as e:
        logger.error(f"[event_tools] diagnose_event_progress error: {e}")
        return json.dumps({"status": "error", "error": str(e)})


# ── Convenience list for the subgraph ───────────────────────────────────────────

def get_event_tools() -> list:
    """Return all tools for the event_related_issue flow."""
    return [
        get_user_events,
        check_event_video_duration,
        check_event_video_config,
        diagnose_event_progress,
    ]
