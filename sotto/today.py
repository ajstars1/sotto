"""Your day, for the right side of the wallpaper.

Two sources, both set in ~/.config/sotto/config.toml:

    [today]
    command = "your-script --json"          # prints {"items": [...], "footer": "..."}
    ics = ["https://calendar.google.com/calendar/ical/.../basic.ics"]   # any iCal link

`command` output: {"items": [{"when": "19:30", "title": "Call Mayank", "flag": "event"}], "footer": "3 drafts wait"}
where flag is "overdue", "today", "event" or "". Items show in the order given.
`ics`: events from now until the end of tomorrow (single events; repeating ones are not expanded yet).
Without either, the wallpaper says how to connect one.
"""
import datetime as dt
import json
import os
import re
import subprocess
import urllib.request
from zoneinfo import ZoneInfo

MAX_ITEMS = 9
MAX_TITLE = 70
FLAGS = {"overdue", "today", "event", ""}


def _clean(s: object, n: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(s or "")).strip()[:n]


def from_command(cmd: str, timeout: float = 25) -> dict:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:200] or f"exit {r.returncode}")
    data = json.loads(r.stdout[-200_000:] or "{}")
    items = []
    for it in data.get("items", [])[:MAX_ITEMS * 2]:
        if not isinstance(it, dict):
            continue
        flag = it.get("flag", "")
        items.append({"when": _clean(it.get("when"), 14), "title": _clean(it.get("title"), MAX_TITLE),
                      "flag": flag if flag in FLAGS else ""})
    return {"items": items, "footer": _clean(data.get("footer"), 60)}


def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for ln in text.splitlines():
        if ln[:1] in (" ", "\t") and out:
            out[-1] += ln[1:]
        else:
            out.append(ln)
    return out


def _when(prop: str, value: str) -> tuple[dt.datetime, bool] | None:
    """(local datetime, all-day) for a DTSTART line."""
    tz = re.search(r"TZID=([^;:]+)", prop)
    try:
        if re.fullmatch(r"\d{8}", value):
            return dt.datetime.strptime(value, "%Y%m%d"), True
        if value.endswith("Z"):
            utc = dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
            return utc.astimezone().replace(tzinfo=None), False
        naive = dt.datetime.strptime(value, "%Y%m%dT%H%M%S")
        if tz:
            return naive.replace(tzinfo=ZoneInfo(tz.group(1))).astimezone().replace(tzinfo=None), False
        return naive, False
    except (ValueError, KeyError):
        return None


def from_ics(urls: list[str], now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now()
    end = dt.datetime.combine(now.date() + dt.timedelta(days=2), dt.time())
    events = []
    for url in urls[:5]:
        req = urllib.request.Request(url, headers={"User-Agent": "sotto-wallpaper"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read(5_000_000).decode("utf-8", errors="replace")
        cur: dict = {}
        for ln in _unfold(text):
            if ln == "BEGIN:VEVENT":
                cur = {}
            elif ln == "END:VEVENT":
                if cur.get("start") and "RRULE" not in cur:
                    events.append(cur)
                cur = {}
            elif ":" in ln:
                prop, value = ln.split(":", 1)
                key = prop.split(";", 1)[0]
                if key == "DTSTART":
                    cur["start"] = _when(prop, value.strip())
                elif key == "SUMMARY":
                    cur["title"] = value.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ")
                elif key == "RRULE":
                    cur["RRULE"] = True
    out = []
    for e in sorted((e for e in events if e["start"]), key=lambda e: e["start"][0]):
        start, all_day = e["start"]
        if all_day:
            if not (now.date() <= start.date() < end.date()):
                continue
        elif not (now - dt.timedelta(minutes=30) <= start < end):
            continue
        day = "" if start.date() == now.date() else "tmrw "
        out.append({"when": day + ("all day" if all_day else start.strftime("%H:%M")),
                    "title": _clean(e.get("title", "(no title)"), MAX_TITLE), "flag": "event"})
    return out


def collect(cfg: dict) -> dict:
    """{"items": [...], "footer": str, "error": str, "configured": bool} for state.json."""
    t = cfg.get("today") or {}
    cmd, ics = t.get("command"), t.get("ics") or []
    if isinstance(ics, str):
        ics = [ics]
    if not cmd and not ics:
        return {"items": [], "footer": "", "error": "", "configured": False}
    items, footer, errors = [], "", []
    if ics:
        try:
            items += from_ics(ics)
        except Exception as e:                  # a calendar being down must not break the wallpaper
            errors.append(f"calendar: {type(e).__name__}")
    if cmd:
        try:
            got = from_command(os.path.expandvars(os.path.expanduser(cmd)))
            items += got["items"]
            footer = got["footer"]
        except Exception as e:
            errors.append(f"today command: {str(e)[:80] or type(e).__name__}")
    return {"items": items[:MAX_ITEMS], "footer": footer, "error": "; ".join(errors), "configured": True}
