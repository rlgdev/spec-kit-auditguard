"""Commands reserved for people: decide, note, sprint open / close (with the seal)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .collectors import run_collectors
from .collectors.base import every, last
from .common import (EXIT_BLOCKED, PROJECT_KEY, AuditGuardError, content_hash, now_iso, parse_date, read_bytes,
                     rel_path, resolve_feature_dir, sha256_json, write_json)
from .context import RunContext, key_of
from .recorder import Recorder
from .reconcile import save_baseline

SUBJECT_VERDICTS = {
    "design": ("approve", "reject"),
    "implement": ("approve", "reject"),
    "pr": ("approve", "reject"),
    "spec-change": ("accept", "reject"),
    "escalation": ("accept", "reject", "defer"),
    "gate": ("approve", "reject"),
}
MILESTONE_OF = {("design", "approve"): "design.signed", ("design", "reject"): "design.reopened",
                ("implement", "approve"): "implement.approved", ("pr", "approve"): "pr.approved"}


def refuse_agent_context(what: str) -> None:
    if os.environ.get("AUDITGUARD_CONTEXT", "").strip().lower() == "agent":
        raise AuditGuardError(f"'auditguard {what}' records a decision of a person and cannot run in an agent context "
                              "(AUDITGUARD_CONTEXT=agent). Ask the user to run it.", EXIT_BLOCKED)


def open_escalations(chain: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for ev in every(chain, ["escalation.raised"]):
        note = (ev.get("data") or {}).get("note")
        latest = last(chain, ["escalation.raised", "escalation.withdrawn", "escalation.decided"], note=note)
        if latest is ev:
            out.append(ev)
    return out


def decide(root: Path, cfg: Any, subject: str, verdict: str, by: Optional[str], role: Optional[str],
           reason: Optional[str], feature_arg: Optional[str]) -> Tuple[Dict[str, Any], List[str]]:
    refuse_agent_context("decide")
    notes: List[str] = []
    base, _, ref = subject.partition(":")
    if base not in SUBJECT_VERDICTS:
        raise AuditGuardError(f"unknown subject '{subject}' - use design, implement, pr, spec-change, "
                              "escalation:<note or id>, gate:<step-id>")
    verdict = verdict.lower()
    if verdict not in SUBJECT_VERDICTS[base]:
        raise AuditGuardError(f"'{verdict}' is not a verdict for {base}: use {' | '.join(SUBJECT_VERDICTS[base])}")
    if base in ("escalation", "gate") and not ref:
        raise AuditGuardError(f"name the {base}: {base}:<{'escalation note path or event id' if base == 'escalation' else 'workflow step id'}>")
    if not by and not os.environ.get("AUDITGUARD_ACTOR"):
        raise AuditGuardError("--by <name> is required: a decision names the person who took it")
    rc = RunContext(root, cfg)
    rec = Recorder(rc)
    feature_dir = resolve_feature_dir(root, feature_arg, required=base != "gate")
    feature = rec.feature_path(feature_dir)
    run_collectors(rec, feature_dir)
    chain = rec.chain(key_of(feature))
    actor = rc.actor_human(by, role)
    data: Dict[str, Any] = {"subject": subject, "verdict": verdict, "by": actor["id"]}
    if role:
        data["role"] = role
    if reason:
        data["reason"] = reason
    if base == "escalation":
        target = None
        for ev in open_escalations(chain):
            d = ev.get("data") or {}
            if str(d.get("note") or "").endswith(ref) or str(ev.get("hash") or "").startswith(ref) or \
                    Path(str(d.get("note") or "")).name == ref:
                target = ev
                break
        if target is None:
            raise AuditGuardError(f"no open escalation matches '{ref}' in {feature or PROJECT_KEY} "
                                  "(auditguard show --open lists them)")
        td = target.get("data") or {}
        spec = {"kind": "escalation.decided", "actor": actor, "source": "cli",
                "data": {**data, "note": td.get("note"), "note_sha256": td.get("note_sha256"), "tool": td.get("tool"),
                         "raised_event": target.get("hash"), "items": td.get("items")}}
        ev = rec.record(feature, spec)
        save_baseline(rc)
        return ev, notes
    if base == "spec-change":
        target = last(chain, ["artefact.changed"], after_handover=True)
        if target is None:
            raise AuditGuardError("no change of the handed-over specification is recorded for this feature")
        data["change_event"] = target.get("hash")
    if base == "design" and cfg.collector_enabled("archiguard"):
        notes.append("archiGuard is installed: its sign-off (archiguard signoff / reopen) is the design milestone; "
                     "this records the decision only")
        ev = rec.record(feature, {"kind": "decision", "actor": actor, "source": "cli", "data": data})
        save_baseline(rc)
        return ev, notes
    if base == "design" and verdict == "approve":
        data["hashes"] = rc.artefact_hashes(feature_dir)
    ev = rec.record(feature, {"kind": "decision", "actor": actor, "source": "cli", "data": data})
    milestone = MILESTONE_OF.get((base, verdict))
    if milestone:
        mdata = {"by": actor["id"], "role": role, "reason": reason, "decision_event": ev["hash"]}
        if milestone == "design.signed":
            mdata["hashes"] = data.get("hashes")
        rec.record(feature, {"kind": milestone, "actor": actor, "source": "cli",
                             "data": {k: v for k, v in mdata.items() if v is not None}})
    save_baseline(rc)
    return ev, notes


def note(root: Path, cfg: Any, text: str, by: Optional[str], feature_arg: Optional[str]) -> Dict[str, Any]:
    refuse_agent_context("note")
    if not text.strip():
        raise AuditGuardError("the note is empty")
    if len(text.encode("utf-8")) > 4096:
        raise AuditGuardError("a note is limited to 4 KB")
    rc = RunContext(root, cfg)
    rec = Recorder(rc)
    feature_dir = resolve_feature_dir(root, feature_arg, required=False) if feature_arg else None
    feature = rec.feature_path(feature_dir)
    return rec.record(feature, {"kind": "note", "actor": rc.actor_human(by), "source": "cli", "data": {"text": text}})


# --------------------------------------------------------------------------- #
# Sprints                                                                       #
# --------------------------------------------------------------------------- #


def sprint_open(root: Path, cfg: Any, sprint_id: str, by: Optional[str], name: Optional[str], start: Optional[str],
                end: Optional[str], goal: Optional[str], close_current: bool) -> Dict[str, Any]:
    refuse_agent_context("sprint open")
    rc = RunContext(root, cfg)
    store = rc.store
    reg = store.register
    cur = reg.open_sprint()
    if cur and cur["id"] != sprint_id:
        if not close_current:
            raise AuditGuardError(f"sprint {cur['id']} is still open - close it first (auditguard sprint close "
                                  f"{cur['id']} --by <name>) or pass --close-current")
        sprint_close(root, cfg, cur["id"], by)
        rc = RunContext(root, cfg)
        store = rc.store
        reg = store.register
    entry = reg.get(sprint_id)
    if entry and entry.get("status") == "closed":
        raise AuditGuardError(f"sprint {sprint_id} is closed and sealed - it cannot be opened again")
    for value, label in ((start, "--start"), (end, "--end")):
        if value and not parse_date(value):
            raise AuditGuardError(f"{label} {value!r} is not a YYYY-MM-DD date")
    if entry is None:
        entry = {"id": sprint_id}
        reg.sprints.append(entry)
    for key, value in (("name", name), ("start", start), ("end", end), ("goal", goal)):
        if value:
            entry[key] = value
    actor = rc.actor_human(by)
    entry["status"] = "open"
    entry["opened"] = {"by": actor["id"], "at": now_iso()}
    problems = reg.problems()
    if problems:
        raise AuditGuardError("the sprint register would be inconsistent:\n  " + "\n  ".join(problems))
    reg.save()
    rec = Recorder(RunContext(root, cfg))
    return rec.record(None, {"kind": "sprint.opened", "actor": actor, "source": "cli",
                             "data": {k: entry.get(k) for k in ("id", "name", "start", "end", "goal")}},
                      sprint=sprint_id)


def sprint_close(root: Path, cfg: Any, sprint_id: Optional[str], by: Optional[str]) -> Dict[str, Any]:
    refuse_agent_context("sprint close")
    from .check import evaluate_rules
    from .hooks import Hooks

    rc = RunContext(root, cfg)
    reg = rc.store.register
    entry = reg.get(sprint_id) if sprint_id else reg.open_sprint()
    if entry is None:
        raise AuditGuardError(f"no sprint {sprint_id!r} in the register" if sprint_id else "no open sprint to close")
    sid = entry["id"]
    if entry.get("status") == "closed" or (rc.store.sprints_dir / sid / "seal.json").is_file():
        raise AuditGuardError(f"sprint {sid} is already closed and sealed")
    # commands stopped without their after_ hook are closed first, so the seal covers them
    Hooks(root, cfg).close_open(reason=f"sprint {sid} closed", only_stopped=True)
    results = evaluate_rules(root, cfg, sprint=sid, closing=True)
    broken = [r for r in results if r["status"] != "ok"]
    if broken and cfg.enforce:
        lines = [f"[FAIL] {r['rule']}: {r['detail']}" for r in broken]
        raise AuditGuardError("sprint close refused (mode: enforce):\n  " + "\n  ".join(lines), 1)
    warnings = [{"rule": r["rule"], "items": r.get("items") or [], "detail": r["detail"]} for r in broken]
    actor = rc.actor_human(by)
    rec = Recorder(RunContext(root, cfg))
    rec.record(None, {"kind": "sprint.closed", "actor": actor, "source": "cli",
                      "data": {"id": sid, "name": entry.get("name"), "start": entry.get("start"), "end": entry.get("end"),
                               "goal": entry.get("goal"), "warnings": warnings}}, sprint=sid)
    seal = build_seal(rc.store, sid, actor["id"], warnings)
    write_json(rc.store.sprints_dir / sid / "seal.json", seal)
    reg = rc.store.register.__class__.load(cfg.register_path)
    entry = reg.get(sid) or entry
    entry["status"] = "closed"
    entry["closed"] = {"by": actor["id"], "at": seal["at"], "seal": seal["hash"]}
    reg.save()
    return seal


def build_seal(store: Any, sprint: str, by: str, warnings: List[Dict[str, Any]], at: Optional[str] = None) -> Dict[str, Any]:
    folder = store.sprints_dir / sprint
    journals: Dict[str, Dict[str, Any]] = {}
    hashes: List[str] = []
    for jdir in sorted(p for p in folder.iterdir() if p.is_dir()):
        jpath = jdir / "journal.jsonl"
        if jpath.is_file():
            events, _ = store.read_journal(jpath)
            journals[f"{jdir.name}/journal.jsonl"] = {
                "head": events[-1].get("hash") if events else None, "count": len(events),
                "first_seq": events[0].get("seq") if events else None, "last_seq": events[-1].get("seq") if events else None,
                "file_sha256": content_hash(read_bytes(jpath))}
        ev_dir = jdir / "evidence"
        if ev_dir.is_dir():
            for f in sorted(ev_dir.iterdir()):
                if f.is_file():
                    hashes.append(content_hash(read_bytes(f)))
    seal: Dict[str, Any] = {"v": 1, "sprint": sprint, "closed_by": by, "at": at or now_iso(), "journals": journals,
                            "evidence": {"count": len(hashes), "sha256_of_sorted_hashes": sha256_json(sorted(hashes))},
                            "warnings": warnings}
    seal["hash"] = sha256_json(seal)
    return seal


def seal_paths(store: Any, sprint: str) -> List[str]:
    return [rel_path(p, store.root) for p in (store.sprints_dir / sprint).rglob("*") if p.is_file()]
