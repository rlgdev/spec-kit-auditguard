"""Shared pieces of the collectors.

A collector reads what other tools left in the repository, compares it with what the chain already
records (the chain is the collector's memory, so a second run with no change emits nothing) and returns
the events to append. It never runs another tool's gates.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..common import read_bytes, rel_path

Evidence = Tuple[str, bytes, Optional[str]]

ITEM_ID_RE = re.compile(r"\b(?:ARCH|SEC|US|UC|FR|NFR|SC|AC|BR|D|H|A\d)-?\d+(?:\.\d+)?\b")


class Emit:
    """One event a collector wants appended (to `feature`'s chain, None = the project chain)."""

    def __init__(self, feature: Optional[str], spec: Dict[str, Any], evidence: Iterable[Evidence] = ()):
        self.feature = feature
        self.spec = spec
        self.evidence = list(evidence)

    @property
    def at(self) -> str:
        return str(self.spec.get("at") or "")


def last(chain: List[Dict[str, Any]], kinds: Iterable[str], **match: Any) -> Optional[Dict[str, Any]]:
    wanted = set(kinds)
    for ev in reversed(chain):
        if ev.get("kind") not in wanted:
            continue
        data = ev.get("data") or {}
        if all(data.get(k) == v for k, v in match.items()):
            return ev
    return None


def every(chain: List[Dict[str, Any]], kinds: Iterable[str], **match: Any) -> List[Dict[str, Any]]:
    wanted = set(kinds)
    out = []
    for ev in chain:
        if ev.get("kind") in wanted and all((ev.get("data") or {}).get(k) == v for k, v in match.items()):
            out.append(ev)
    return out


def mtime_iso(path: Path) -> Optional[str]:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return _dt.datetime.fromtimestamp(int(ts)).astimezone().isoformat()


def evidence_of(path: Path, root: Path, name: Optional[str] = None) -> Evidence:
    return (name or path.name, read_bytes(path), rel_path(path, root))


def item_ids(text: str, limit: int = 30) -> List[str]:
    seen: List[str] = []
    for m in ITEM_ID_RE.finditer(text):
        if m.group(0) not in seen:
            seen.append(m.group(0))
        if len(seen) >= limit:
            break
    return seen
