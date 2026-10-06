"""Waiver history: scope deferrals (Scope Coverage), conformance deviations (Architecture Conformance) and the
decision ledger (waivers; ADR status changes become decisions). Every change of a waiver is one event."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..common import (cell, clean_cell, column, first_table, parse_date, read_text, rel_path, section_lines,
                      sha256_text, today)
from .base import Emit, every, last

DEFERRED_WORDS = ("deferred", "defer", "out of scope", "out-of-scope", "outofscope", "excluded", "descoped",
                  "de-scoped", "postponed", "waived", "later", "not in scope", "won't do", "wont do", "dropped")
DEFERRED_GLYPHS = ("⏸", "❌", "✖", "✗")
ID_RE = re.compile(r"\b(?:US|UC|FR|NFR|SC|AC|BR|D)-?\d+\b")
DECISION_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")
FIELDS = ("status", "reason", "owner", "approver", "expires", "rules", "kind", "adr", "title")
STATUS_KINDS = {"approved": "waiver.approved", "revoked": "waiver.revoked", "superseded": "waiver.superseded"}


def _deferred(status: str) -> bool:
    low = status.lower()
    return any(g in status for g in DEFERRED_GLYPHS) or any(w in low for w in DEFERRED_WORDS)


def scope_deferrals(feature_dir: Path, root: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for artefact, suffix in (("plan.md", ""), ("tasks.md", "@tasks")):
        path = feature_dir / artefact
        if not path.is_file():
            continue
        sec = section_lines(read_text(path), r"^scope coverage\b")
        if not sec:
            continue
        header, rows = first_table(sec[1], sec[0])
        id_col = column(header, "id")
        st_col = column(header, "status", "decision", "coverage", "state")
        reason_col = column(header, "reason")
        if id_col is None or st_col is None:
            continue
        for line_no, cells in rows:
            status = cell(cells, st_col)
            if not _deferred(status):
                continue
            reason = cell(cells, reason_col) or None
            for item in ID_RE.findall(cell(cells, id_col)):
                wid = f"defer:{item}{suffix}"
                out[wid] = {"id": wid, "source": "scope", "item": item, "status": "deferred", "reason": reason,
                            "artefact": rel_path(path, root), "location": f"{artefact}:{line_no}",
                            "row": "| " + " | ".join(cells) + " |"}
    return out


def conformance_rows(feature_dir: Path, root: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    path = feature_dir / "plan.md"
    if not path.is_file():
        return out
    sec = section_lines(read_text(path), r"^architecture conformance\b")
    if not sec:
        return out
    header, rows = first_table(sec[1], sec[0])
    rule_col = column(header, "rule", "id")
    st_col = column(header, "status")
    reason_col = column(header, "adr", "reason", "justification")
    title_col = column(header, "title")
    if rule_col is None or st_col is None:
        return out
    for line_no, cells in rows:
        status = cell(cells, st_col).lower()
        if "deviation" in status:
            kind = "deviation"
        elif "not applicable" in status or status in ("n/a", "na", "not-applicable"):
            kind = "not-applicable"
        else:
            continue
        rule = cell(cells, rule_col)
        reason = cell(cells, reason_col) or None
        wid = f"conf:{rule}"
        out[wid] = {"id": wid, "source": "conformance", "rule": rule, "rules": [rule], "kind": kind, "status": kind,
                    "reason": reason, "adr": DECISION_ID_RE.findall(reason or "") or None,
                    "title": cell(cells, title_col) or None, "artefact": rel_path(path, root),
                    "location": f"plan.md:{line_no}", "row": "| " + " | ".join(cells) + " |"}
    return out


def ledger_entries(path: Optional[Path]) -> Dict[str, Tuple[Dict[str, Any], str]]:
    """Latest entry per id (a later line with the same id supersedes) with its raw line."""
    out: Dict[str, Tuple[Dict[str, Any], str]] = {}
    if path is None or not path.is_file():
        return out
    for raw in read_text(path).split("\n"):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            out[entry["id"]] = (entry, raw)
    return out


def _scoped_to(entry: Dict[str, Any], feature: Optional[str]) -> Optional[bool]:
    """True: belongs to this feature's chain; None: unscoped (project chain); False: another feature's."""
    feats = (entry.get("scope") or {}).get("features") or []
    if not feats:
        return None
    if feature is None:
        return False
    name = Path(feature).name
    return any(f == feature or f == name or feature.endswith("/" + str(f)) for f in feats)


def _record_state(chain: List[Dict[str, Any]], source_filter: Optional[str] = None) -> Dict[str, Optional[Dict[str, Any]]]:
    state: Dict[str, Optional[Dict[str, Any]]] = {}
    for ev in chain:
        kind = ev.get("kind") or ""
        if not kind.startswith("waiver."):
            continue
        data = ev.get("data") or {}
        if source_filter and data.get("source") != source_filter:
            continue
        wid = data.get("id")
        if not wid:
            continue
        state[wid] = None if kind == "waiver.removed" else (data.get("record") or state.get(wid))
    return state


def _diff(chain: List[Dict[str, Any]], current: Dict[str, Dict[str, Any]], sources: Tuple[str, ...],
          feature: Optional[str], actor: Dict[str, Any], ledger_lines: Dict[str, str]) -> List[Emit]:
    prev_state = {k: v for k, v in _record_state(chain).items() if _source_of(k) in sources}
    out: List[Emit] = []
    day = today()
    for wid in sorted(set(prev_state) | set(current)):
        prev = prev_state.get(wid)
        cur = current.get(wid)
        src = (cur or prev or {}).get("source") or _source_of(wid)
        if src not in sources:
            continue
        base = {"id": wid, "source": src}
        extra = {"ledger_line": ledger_lines[wid]} if wid in ledger_lines else {}
        if cur and not prev:
            out.append(_emit(feature, "waiver.added", actor, {**base, **_flat(cur), "record": cur, **extra}))
        elif prev and not cur:
            out.append(_emit(feature, "waiver.removed", actor, {**base, **_flat(prev), "record": None, "before": prev}))
            continue
        elif cur and prev:
            changed = [f for f in FIELDS if prev.get(f) != cur.get(f)]
            if changed:
                kind = STATUS_KINDS.get(str(cur.get("status"))) if "status" in changed else None
                kind = kind or "waiver.changed"
                out.append(_emit(feature, kind, actor, {
                    **base, **_flat(cur), "record": cur, "changed_fields": changed,
                    "before": {f: prev.get(f) for f in changed}, "after": {f: cur.get(f) for f in changed}, **extra}))
        if cur:
            expires = parse_date(cur.get("expires"))
            if expires and expires < day and cur.get("status") not in ("revoked", "superseded"):
                done = [e for e in every(chain, ["waiver.expired"], id=wid) if (e.get("data") or {}).get("expires") == cur.get("expires")]
                if not done:
                    out.append(_emit(feature, "waiver.expired", actor, {**base, **_flat(cur), "record": cur}))
    return out


def _source_of(wid: str) -> str:
    if wid.startswith("defer:"):
        return "scope"
    if wid.startswith("conf:"):
        return "conformance"
    return "ledger"


def _flat(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {k: rec.get(k) for k in ("item", "rule", "rules", "kind", "reason", "owner", "approver", "expires", "status",
                                     "location") if rec.get(k) is not None}


def _emit(feature: Optional[str], kind: str, actor: Dict[str, Any], data: Dict[str, Any]) -> Emit:
    return Emit(feature, {"kind": kind, "actor": actor, "source": f"collector:waivers:{data.get('source')}", "data": data})


def collect(rc: Any, feature_dir: Optional[Path], feature: Optional[str], chain: List[Dict[str, Any]],
            project_chain: List[Dict[str, Any]]) -> List[Emit]:
    root = rc.root
    out: List[Emit] = []
    actor = rc.actor_script("auditguard")
    if feature_dir is not None:
        current = scope_deferrals(feature_dir, root)
        current.update(conformance_rows(feature_dir, root))
        out += _diff(chain, current, ("scope", "conformance"), feature, actor, {})

    conf = rc.cfg.collector("archiguard")
    ledger_path = rc.cfg.path(conf.get("ledger")) if conf.get("enabled", True) is not False else None
    entries = ledger_entries(ledger_path)
    if not entries:
        return out
    ledger_actor = rc.actor_script("archiguard-ledger")
    targets: List[Tuple[Optional[str], List[Dict[str, Any]]]] = [(feature, chain)] if feature else []
    targets.append((None, project_chain))
    for target_feature, target_chain in targets:
        waivers: Dict[str, Dict[str, Any]] = {}
        lines: Dict[str, str] = {}
        decisions: List[Tuple[Dict[str, Any], str]] = []
        for eid, (entry, raw) in sorted(entries.items()):
            scoped = _scoped_to(entry, target_feature)
            if target_feature is None and scoped is not None:
                continue
            if target_feature is not None and scoped is not True:
                continue
            if entry.get("type") == "waiver":
                waivers[eid] = {"id": eid, "source": "ledger", "type": "waiver", "status": entry.get("status"),
                                "title": entry.get("title"), "rules": entry.get("rules") or [], "owner": entry.get("owner"),
                                "approver": entry.get("approver"), "expires": entry.get("expires"),
                                "evidence": entry.get("evidence"), "ledger_hash": entry.get("hash"),
                                "reason": entry.get("title")}
                lines[eid] = raw
            else:
                decisions.append((entry, raw))
        out += _diff(target_chain, waivers, ("ledger",), target_feature, ledger_actor, lines)
        for entry, raw in decisions:
            subject = f"ledger:{entry['id']}"
            prev = last(target_chain, ["decision"], subject=subject)
            if prev is not None and (prev.get("data") or {}).get("ledger_hash") == entry.get("hash"):
                continue
            out.append(Emit(target_feature, {
                "kind": "decision", "at": None, "actor": ledger_actor, "source": "collector:archiguard-ledger",
                "data": {"subject": subject, "verdict": entry.get("status"), "type": entry.get("type"),
                         "title": entry.get("title"), "rules": entry.get("rules"), "by": entry.get("approver") or entry.get("owner"),
                         "owner": entry.get("owner"), "approver": entry.get("approver"), "expires": entry.get("expires"),
                         "ledger_hash": entry.get("hash"), "ledger_line": raw,
                         "fingerprint": sha256_text(raw)}}))
    return out


def current_waivers(chain: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {k: v for k, v in _record_state(chain).items() if v}


def row_text(cells: List[str]) -> str:
    return " | ".join(clean_cell(c) for c in cells)
