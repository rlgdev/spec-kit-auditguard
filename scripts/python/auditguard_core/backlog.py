"""`auditguard backlog`: the loop-feedback items as backlog candidates, merged across features (FR-505).

Read-only: it records nothing and may run in an agent context. The rows come from the chains (feedback events and
the `decide feedback:<id>` decisions); the formats are Markdown (one table per addressee), CSV (columns a tracker
imports) and JSON (every field).
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List, Optional, Sequence

from . import feedback as fb
from .common import AuditGuardError, rel_path

CSV_COLUMNS = ("External ID", "Summary", "Description", "Priority", "Labels", "Status", "Ref", "Features",
               "First sprint", "Evidence")
DEFAULT_STATUSES = ("new", "accepted")
FORMATS = ("md", "csv", "json")


def parse_statuses(value: Optional[str]) -> Sequence[str]:
    if not value:
        return DEFAULT_STATUSES
    parts = [p.strip().lower() for p in value.split(",") if p.strip()]
    if "all" in parts:
        return fb.STATUSES
    bad = [p for p in parts if p not in fb.STATUSES]
    if bad:
        raise AuditGuardError(f"unknown status {', '.join(bad)} - use {', '.join(fb.STATUSES)} or all")
    return tuple(parts)


def records(store: Any, sprint: Optional[str] = None, statuses: Sequence[str] = fb.STATUSES) -> List[Dict[str, Any]]:
    chains = {k: store.load_chain(k) for k in store.chain_keys()}
    out = [r for r in fb.items(chains).values() if fb.in_sprint(r, sprint) and r.get("status") in statuses]
    for r in out:
        r["evidence_path"] = evidence_path(store, chains, r)
    return sorted(out, key=fb.sort_key)


def evidence_path(store: Any, chains: Dict[str, List[Dict[str, Any]]], rec: Dict[str, Any]) -> Optional[str]:
    """The trail path of the loop-feedback.json snapshot behind the item's latest event."""
    ev = rec.get("last_event")
    if not ev:
        return None
    fp = (ev.get("data") or {}).get("file_fingerprint")
    key = str(ev.get("feature") or "").rsplit("/", 1)[-1]
    for saved in reversed(chains.get(key) or []):
        d = saved.get("data") or {}
        if saved.get("kind") == "report.saved" and d.get("name") == fb.FILE_NAME and (fp is None or d.get("fingerprint") == fp):
            for ref in saved.get("evidence") or []:
                path = store.evidence_path(saved, ref)
                if path is not None:
                    return rel_path(path, store.root)
    return None


def labels(cfg: Any, rec: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for label in list(cfg.get("backlog", "labels", default=["loop-feedback"]) or []) + [rec.get("kind")] + \
            fb.subject_contexts(rec.get("kind"), rec.get("subject")) + list(rec.get("home_contexts") or []):
        if label and str(label) not in out:
            out.append(str(label))
    return out


def priority(cfg: Any, rec: Dict[str, Any]) -> str:
    mapping = cfg.get("backlog", "priority", default={}) or {}
    impact = str(rec.get("impact") or "")
    return str(mapping.get(impact) or impact.capitalize())


def description(rec: Dict[str, Any]) -> str:
    parts = [str(rec.get("observation") or rec.get("title") or "").strip()]
    if rec.get("suggestion"):
        parts.append(f"Suggestion: {rec['suggestion']}")
    if rec.get("target"):
        parts.append(f"Target: {rec['target']}.")
    roles = fb.for_roles(rec)
    if roles:
        parts.append(f"For: {', '.join(roles)}.")
    waiver = rec.get("waiver")
    if isinstance(waiver, dict) and waiver.get("id"):
        parts.append(f"Waiver: {waiver['id']}" + (f" until {waiver['expires']}" if waiver.get("expires") else "") + ".")
    return " ".join(p for p in parts if p)


def to_csv(cfg: Any, rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([r["id"], r.get("title") or "", description(r), priority(cfg, r), ";".join(labels(cfg, r)),
                         r.get("status"), r.get("ref") or "", ";".join(r.get("features") or []),
                         r.get("first_sprint") or "", r.get("evidence_path") or ""])
    return buf.getvalue()


def _decision_json(ev: Dict[str, Any]) -> Dict[str, Any]:
    d = ev.get("data") or {}
    return {"verdict": d.get("verdict"), "by": d.get("by"), "role": d.get("role"), "reason": d.get("reason"),
            "ref": d.get("ref"), "at": ev.get("at"), "sprint": ev.get("sprint"),
            "chain": str(ev.get("feature") or "_project")}


def to_json(cfg: Any, rows: List[Dict[str, Any]]) -> str:
    out = []
    for r in rows:
        item = {k: r.get(k) for k in ("id",) + fb.ITEM_FIELDS + ("occurrences", "first_seen", "map_version")}
        item.update({"features": r.get("features") or [], "open_in": r.get("open_in") or [],
                     "sprints": r.get("sprints") or [], "first_sprint": r.get("first_sprint"),
                     "home_contexts": r.get("home_contexts") or [], "status": r.get("status"), "ref": r.get("ref"),
                     "priority": priority(cfg, r), "labels": labels(cfg, r), "evidence": r.get("evidence_path"),
                     "decisions": [_decision_json(ev) for ev in r.get("decisions") or []]})
        out.append(item)
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def to_md(cfg: Any, rows: List[Dict[str, Any]], title: str, scope: str) -> str:
    lines = [f"# {title}", "", scope, ""]
    if not rows:
        lines += ["No loop-feedback item.", ""]
        return "\n".join(lines)
    by_status: Dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    lines.append("**Items:** " + " · ".join(f"{n} {s}" for s, n in sorted(by_status.items(), key=lambda x: fb.STATUSES.index(x[0]))))
    lines.append("")
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        for role in fb.for_roles(r) or ["(no addressee)"]:
            groups.setdefault(role, []).append(r)
    for role in sorted(groups):
        lines.append(f"## For: {role}")
        lines.append("")
        lines.append("| Id | Impact | Kind | Subject | Suggestion for the next iteration | Features | Status | Ref |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in groups[role]:
            lines.append("| " + " | ".join(_cell(v) for v in (
                r["id"], r.get("impact"), r.get("kind"), r.get("subject"), r.get("suggestion"),
                ", ".join(str(f).rsplit("/", 1)[-1] for f in r.get("features") or []), r.get("status"), r.get("ref") or "")) + " |")
        lines.append("")
    return "\n".join(lines)


def render(store: Any, cfg: Any, *, sprint: Optional[str] = None, statuses: Sequence[str] = DEFAULT_STATUSES,
           fmt: str = "md") -> str:
    if fmt not in FORMATS:
        raise AuditGuardError(f"unknown format {fmt!r} - use {', '.join(FORMATS)}")
    rows = records(store, sprint, statuses)
    if fmt == "csv":
        return to_csv(cfg, rows)
    if fmt == "json":
        return to_json(cfg, rows)
    scope = (f"Sprint {sprint}" if sprint else "All sprints") + f" · status {', '.join(statuses)} · " \
        "from the loop feedback archiGuard feeds back to the domain master design (auditguard backlog)."
    return to_md(cfg, rows, "Loop feedback backlog", scope)


def sprint_view(store: Any, cfg: Any, sprint: str) -> Optional[str]:
    """audit/sprints/<S>/loop-feedback.md: the sprint's backlog candidates with their triage status (None: no item)."""
    rows = records(store, sprint, fb.STATUSES)
    if not rows:
        return None
    scope = ("Every loop-feedback item with an event or a decision in this sprint, with its triage status now. "
             "Generated by auditGuard from the journals - the journals are the record; this page is a view.")
    return to_md(cfg, rows, f"Loop feedback — Sprint {sprint}", scope)
