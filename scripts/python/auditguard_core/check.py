"""Completeness rules (`auditguard check`, evaluated at `sprint close`): [OK] / [WARN] in record mode / [FAIL] in enforce."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .collectors.waivers import current_waivers
from .common import PROJECT_KEY, UNASSIGNED, resolve_feature_dir
from .context import RunContext
from .decisions import open_escalations

RULE_TEXT = {
    "implement_requires_design_signed": "no /speckit.implement before a signed design",
    "waiver_requires_approver": "every waiver in force has an approver (ledger) or a reason (deferral, deviation)",
    "escalations_decided_before_close": "every escalation is decided (or withdrawn) before the sprint closes",
    "no_changes_after_signoff": "no design artefact changed after the sign-off without re-opening the design",
    "commands_closed_before_close": "every started command finished or was closed",
    "no_unassigned_events": "every event belongs to a sprint",
    "anchored_before_export": "a sprint is anchored in git before it is exported",
}


def open_commands(chain: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    started = {e.get("hash"): e for e in chain if e.get("kind") == "command.started"}
    for e in chain:
        if e.get("kind") in ("command.finished", "command.abandoned"):
            started.pop((e.get("data") or {}).get("started_event"), None)
    # a finish without a link closes the latest start of the same command before it
    for e in chain:
        if e.get("kind") in ("command.finished", "command.abandoned") and not (e.get("data") or {}).get("started_event"):
            cands = [s for s in started.values() if s.get("command") == e.get("command") and s.get("seq", 0) < e.get("seq", 0)]
            if cands:
                started.pop(max(cands, key=lambda s: s.get("seq", 0)).get("hash"), None)
    return sorted(started.values(), key=lambda s: s.get("seq", 0))


def evaluate_rules(root: Path, cfg: Any, *, sprint: Optional[str] = None, feature_arg: Optional[str] = None,
                   closing: bool = False, rules: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    rc = RunContext(root, cfg)
    store = rc.store
    keys = store.chain_keys()
    if feature_arg:
        fd = resolve_feature_dir(root, feature_arg)
        keys = [fd.name] if fd is not None else []
    chains = {k: store.load_chain(k) for k in keys}

    def in_scope(ev: Dict[str, Any]) -> bool:
        return sprint is None or ev.get("_sprint_dir") == sprint

    touched = {k for k, ch in chains.items() if any(in_scope(e) for e in ch)}
    enabled = cfg.get("rules") or {}
    results: List[Dict[str, Any]] = []

    def add(rule: str, items: List[str], detail_ok: str, detail_bad: str) -> None:
        if rules is not None and rule not in rules:
            return
        if not enabled.get(rule, False):
            return
        status = "ok" if not items else ("fail" if cfg.enforce else "warn")
        results.append({"rule": rule, "text": RULE_TEXT[rule], "status": status, "items": items,
                        "detail": detail_ok if not items else f"{detail_bad}: {', '.join(items[:8])}"
                        + (f" (+{len(items) - 8} more)" if len(items) > 8 else "")})

    # implement before a signed design
    items = []
    for key, chain in chains.items():
        signed = False
        for e in chain:
            k = e.get("kind")
            if k == "design.signed":
                signed = True
            elif k == "design.reopened":
                signed = False
            elif k == "command.started" and e.get("command") == "speckit.implement" and not signed and in_scope(e):
                items.append(f"{key} #{e.get('seq')}")
    add("implement_requires_design_signed", items, "every implement started on a signed design",
        "implement started without a signed design")

    # waivers in force without approver / reason
    items = []
    for key in sorted(touched):
        for wid, rec in sorted(current_waivers(chains[key]).items()):
            if rec.get("source") == "ledger":
                if rec.get("status") == "approved" and not rec.get("approver"):
                    items.append(f"{key}: {wid} (no approver)")
            elif not rec.get("reason"):
                items.append(f"{key}: {wid} (no reason)")
    add("waiver_requires_approver", items, "every waiver in force names an approver or a reason",
        "waivers without an approver or reason")

    # open escalations
    items = []
    for key in sorted(touched):
        for e in open_escalations(chains[key]):
            items.append(f"{key}: {(e.get('data') or {}).get('note')}")
    add("escalations_decided_before_close", items, "no open escalation", "escalations not decided")

    # changes after the sign-off
    items = []
    for key, chain in chains.items():
        for e in chain:
            if e.get("kind") == "artefact.changed" and (e.get("data") or {}).get("after_signoff") and in_scope(e):
                files = [c.get("path") for c in e.get("changed") or []]
                items.append(f"{key} #{e.get('seq')} ({', '.join(Path(str(f)).name for f in files[:3])})")
    add("no_changes_after_signoff", items, "no artefact changed after a sign-off", "changes after the sign-off")

    # open commands
    items = []
    for key in sorted(touched):
        for e in open_commands(chains[key]):
            items.append(f"{key}: {e.get('command')} #{e.get('seq')}")
    local = rc.state("open.json", {}) or {}
    for entry in local.get("open") or []:
        label = f"{Path(str(entry.get('feature'))).name if entry.get('feature') else PROJECT_KEY}: {entry.get('command')} (open on this workstation)"
        if label not in items:
            items.append(label)
    add("commands_closed_before_close", items, "every started command is closed", "commands still open")

    # unassigned events
    items = []
    if UNASSIGNED in store.sprint_dirs():
        for key in store.chain_keys():
            path = store.journal_path(UNASSIGNED, key)
            if path.is_file():
                n = len(store.read_journal(path)[0])
                items.append(f"{key}: {n} event(s)")
    add("no_unassigned_events", items, "every event belongs to a sprint", f"events in {UNASSIGNED}")
    return results


def render_results(results: List[Dict[str, Any]], scope: str) -> str:
    from . import __version__
    lines = [f"auditGuard {__version__} | check | {scope}"]
    for r in results:
        tag = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}[r["status"]]
        lines.append(f"  {tag} {r['rule']:<34} {r['detail']}")
    bad = [r for r in results if r["status"] != "ok"]
    lines.append("")
    lines.append(f"RESULT: {'PASS' if not bad else ('FAIL' if any(r['status'] == 'fail' for r in bad) else 'WARN')} | "
                 f"{len(results) - len(bad)} ok, {len(bad)} broken")
    return "\n".join(lines)
