"""The sprint register (audit/sprints.yml): the source of truth for sprints, their order and their status."""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import yamlio
from .common import UNASSIGNED, AuditGuardError, parse_date, read_text, today, write_text

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$")
STATUSES = ("planned", "open", "closed")
FIELD_ORDER = ("id", "name", "start", "end", "goal", "status", "opened", "closed")

HEADER = """# auditGuard sprint register - the source of truth for sprints (order = chronological).
# Change it through the CLI so every change is also recorded in the audit trail:
#   auditguard sprint open <id> --name "<name>" --start YYYY-MM-DD --end YYYY-MM-DD --by "<name>"
#   auditguard sprint close [<id>] --by "<name>"
"""


class Register:
    def __init__(self, path: Path, sprints: List[Dict[str, Any]], exists: bool):
        self.path = path
        self.sprints = sprints
        self.exists = exists

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: Path) -> "Register":
        if not path.is_file():
            return cls(path, [], False)
        data = yamlio.load_file(path)
        raw = data.get("sprints") or []
        if not isinstance(raw, list):
            raise AuditGuardError(f"{path}: 'sprints' must be a list")
        sprints = []
        for item in raw:
            if not isinstance(item, dict):
                raise AuditGuardError(f"{path}: every sprint must be a mapping")
            entry = dict(item)
            for key in ("start", "end"):
                if entry.get(key) is not None:
                    entry[key] = str(entry[key])[:10]
            entry["id"] = str(entry.get("id") or "")
            entry["status"] = str(entry.get("status") or "planned")
            sprints.append(entry)
        return cls(path, sprints, True)

    def save(self) -> None:
        ordered = []
        for s in self.sprints:
            item = {k: s[k] for k in FIELD_ORDER if k in s and s[k] is not None}
            for k, v in s.items():
                if k not in item and v is not None:
                    item[k] = v
            ordered.append(item)
        body = yamlio.dumps({"version": 1, "sprints": ordered}) if ordered else "version: 1\nsprints: []\n"
        write_text(self.path, HEADER + body)
        self.exists = True

    # ----------------------------------------------------------------- query
    def ids(self) -> List[str]:
        return [s["id"] for s in self.sprints]

    def get(self, sprint_id: str) -> Optional[Dict[str, Any]]:
        for s in self.sprints:
            if s["id"] == sprint_id:
                return s
        return None

    def open_sprint(self) -> Optional[Dict[str, Any]]:
        for s in self.sprints:
            if s.get("status") == "open":
                return s
        return None

    def by_date(self, day: _dt.date) -> Optional[Dict[str, Any]]:
        for s in self.sprints:
            start, end = parse_date(s.get("start")), parse_date(s.get("end"))
            if start and end and start <= day <= end:
                return s
        return None

    def current(self, day: Optional[_dt.date] = None) -> Optional[Dict[str, Any]]:
        return self.open_sprint() or self.by_date(day or today())

    def order_key(self, sprint_id: str) -> Tuple[int, str]:
        ids = self.ids()
        if sprint_id in ids:
            return (ids.index(sprint_id), sprint_id)
        if sprint_id == UNASSIGNED:
            return (len(ids) + 1, sprint_id)
        return (len(ids), sprint_id)

    # -------------------------------------------------------------- validate
    def problems(self) -> List[str]:
        out: List[str] = []
        seen = set()
        opened = [s["id"] for s in self.sprints if s.get("status") == "open"]
        if len(opened) > 1:
            out.append(f"more than one open sprint: {', '.join(opened)}")
        prev_end: Optional[_dt.date] = None
        prev_id = None
        for s in self.sprints:
            sid = s["id"]
            if not ID_RE.match(sid) or sid.startswith("_"):
                out.append(f"sprint id {sid!r} is not valid (letters, digits, . _ -; 2-32 characters; not starting with _)")
            if sid in seen:
                out.append(f"sprint id {sid} appears twice")
            seen.add(sid)
            if s.get("status") not in STATUSES:
                out.append(f"sprint {sid}: status {s.get('status')!r} must be one of {', '.join(STATUSES)}")
            start, end = parse_date(s.get("start")), parse_date(s.get("end"))
            if s.get("start") and not start:
                out.append(f"sprint {sid}: start {s.get('start')!r} is not a date")
            if s.get("end") and not end:
                out.append(f"sprint {sid}: end {s.get('end')!r} is not a date")
            if start and end and start > end:
                out.append(f"sprint {sid}: start {start} is after end {end}")
            if start and prev_end and start <= prev_end:
                out.append(f"sprint {sid} starts on {start}, before sprint {prev_id} ends ({prev_end}) - "
                           "sprints must not overlap and must be listed in chronological order")
            if end:
                prev_end, prev_id = end, sid
            if s.get("status") == "closed" and not (s.get("closed") or {}).get("seal"):
                out.append(f"sprint {sid} is closed but the register records no seal")
        return out

    def raw_text(self) -> str:
        return read_text(self.path) if self.path.is_file() else ""
