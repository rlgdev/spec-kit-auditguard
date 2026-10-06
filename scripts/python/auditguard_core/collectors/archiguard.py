"""archiGuard collector: combined verdicts, escalation notes, design sign-off / re-open, handover 4->5, test loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common import content_hash, read_bytes, read_text, rel_path
from .base import Emit, evidence_of, every, item_ids, last, mtime_iso

GATES = "gates"
SPECIAL = {"signoff.json", "handover-4-5.json", "test-loop.json", "applicable-rules.json"}


def _json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(read_text(path))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def verdict_files(feature_dir: Path) -> List[Path]:
    gates = feature_dir / GATES
    if not gates.is_dir():
        return []
    return sorted(p for p in gates.glob("*.json") if p.name not in SPECIAL)


def escalation_notes(feature_dir: Path) -> List[Path]:
    gates = feature_dir / GATES
    return sorted(gates.glob("escalation-*.md")) if gates.is_dir() else []


def collect(rc: Any, feature_dir: Path, feature: str, chain: List[Dict[str, Any]]) -> List[Emit]:
    root = rc.root
    out: List[Emit] = []
    actor = rc.actor_script("archiguard")

    # combined step verdicts: gates/<command>-<a|b|verify>.json
    for path in verdict_files(feature_dir):
        data = _json(path)
        if not data or data.get("tool") != "archiguard" or "status" not in data or "step" not in data:
            continue
        step = path.stem
        fp = content_hash(read_bytes(path))
        prev = last(chain, ["gate.verdict"], tool="archiguard", step=step)
        if prev is not None and (prev.get("data") or {}).get("fingerprint") == fp:
            continue
        gates = data.get("gates") or []
        evidence = [evidence_of(path, root)]
        for g in gates:
            ref = g.get("verdict")
            if ref and (feature_dir / ref).is_file():
                evidence.append(evidence_of(feature_dir / ref, root, Path(ref).name))
        esc = data.get("escalation") or None
        out.append(Emit(feature, {
            "kind": "gate.verdict", "at": mtime_iso(path), "actor": actor, "source": "collector:archiguard",
            "data": {
                "tool": "archiguard", "step": step, "command": data.get("command"), "status": data.get("status"),
                "mode": data.get("mode"),
                "blocking": sum(int(g.get("blocking") or 0) for g in gates),
                "advisory": sum(int(g.get("advisory") or 0) for g in gates),
                "waived": sum(int(g.get("waived") or 0) for g in gates),
                "iterations": data.get("iteration"), "max_iterations": data.get("max_iterations"),
                "history": [{"iteration": h.get("iteration"), "status": h.get("status")} for h in data.get("history") or []],
                "checks": [{"gate": g.get("gate"), "check": g.get("check"), "status": g.get("status")} for g in gates],
                "escalation": esc.get("reason") if isinstance(esc, dict) else None,
                "verdict_commit": data.get("commit"), "pins": data.get("pins"),
                "source_file": rel_path(path, root), "fingerprint": fp,
            }}, evidence))

    # escalation notes
    out += escalation_emits(rc, feature_dir, feature, chain, escalation_notes(feature_dir), "archiguard")

    # design sign-off and re-open (gates/signoff.json, gates/signoff-history/*.json)
    records: List[tuple] = []
    hist = feature_dir / GATES / "signoff-history"
    if hist.is_dir():
        for p in sorted(hist.glob("*.json")):
            rec = _json(p)
            if rec:
                records.append(((rec.get("reopened") or {}).get("at") or rec.get("at") or "", p, rec))
    current = feature_dir / GATES / "signoff.json"
    if current.is_file():
        rec = _json(current)
        if rec:
            records.append((rec.get("at") or "", current, rec))
    known = {(ev.get("data") or {}).get("fingerprint") for ev in every(chain, ["decision"], subject="design")}
    for _, path, rec in sorted(records, key=lambda r: str(r[0])):
        signed_fp = content_hash(json.dumps({k: v for k, v in rec.items() if k != "reopened"}, sort_keys=True).encode())
        if signed_fp not in known:
            # the sign-off itself (also when only its re-opened copy is left)
            known.add(signed_fp)
            data = {"subject": "design", "verdict": "approve", "by": rec.get("by"), "role": rec.get("role"),
                    "hashes": rec.get("hashes"), "pins": rec.get("pins"), "evidence_status": rec.get("evidence"),
                    "signed_commit": rec.get("commit"), "source_file": rel_path(current, root),
                    "fingerprint": signed_fp}
            out.append(Emit(feature, {"kind": "decision", "at": rec.get("at"), "actor": actor,
                                      "source": "collector:archiguard", "data": data},
                            [evidence_of(path, root, "signoff.json")]))
            out.append(Emit(feature, {"kind": "design.signed", "at": rec.get("at"), "actor": actor,
                                      "source": "collector:archiguard",
                                      "data": {"by": rec.get("by"), "role": rec.get("role"), "hashes": rec.get("hashes"),
                                               "pins": rec.get("pins"), "signed_commit": rec.get("commit"),
                                               "fingerprint": signed_fp}}))
        reopened = rec.get("reopened")
        if isinstance(reopened, dict):
            fp = content_hash(json.dumps(reopened, sort_keys=True).encode() + signed_fp.encode())
            if fp not in known:
                known.add(fp)
                out.append(Emit(feature, {"kind": "decision", "at": reopened.get("at"), "actor": actor,
                                          "source": "collector:archiguard",
                                          "data": {"subject": "design", "verdict": "reopen", "by": reopened.get("by"),
                                                   "reason": reopened.get("reason"), "fingerprint": fp,
                                                   "source_file": rel_path(path, root)}},
                                [evidence_of(path, root)]))
                out.append(Emit(feature, {"kind": "design.reopened", "at": reopened.get("at"), "actor": actor,
                                          "source": "collector:archiguard",
                                          "data": {"by": reopened.get("by"), "reason": reopened.get("reason"),
                                                   "fingerprint": fp}}))

    # handover 4 -> 5 and the test loop
    for name, kind, step in (("handover-4-5.json", "handover.4-5", "handover-4-5"), ("test-loop.json", "test.exit", "test-loop")):
        path = feature_dir / GATES / name
        if not path.is_file():
            continue
        rec = _json(path) or {}
        fp = content_hash(read_bytes(path))
        prev = last(chain, ["gate.verdict"], tool="archiguard", step=step)
        if prev is not None and (prev.get("data") or {}).get("fingerprint") == fp:
            continue
        status = rec.get("status")
        out.append(Emit(feature, {"kind": "gate.verdict", "at": mtime_iso(path), "actor": actor,
                                  "source": "collector:archiguard",
                                  "data": {"tool": "archiguard", "step": step, "status": status,
                                           "verdict_commit": rec.get("commit") or (rec.get("subject") or {}).get("commit"),
                                           "source_file": rel_path(path, root), "fingerprint": fp}},
                        [evidence_of(path, root)]))
        if status in ("pass", "waived") and not last(chain, [kind], fingerprint=fp):
            out.append(Emit(feature, {"kind": kind, "at": mtime_iso(path), "actor": actor,
                                      "source": "collector:archiguard",
                                      "data": {"status": status, "fingerprint": fp,
                                               "verdict_commit": rec.get("commit") or (rec.get("subject") or {}).get("commit")}}))

    # saved reports
    for name in ("architecture-compliance.md", "analyze-report.md"):
        path = feature_dir / GATES / name
        if not path.is_file():
            continue
        fp = content_hash(read_bytes(path))
        if last(chain, ["report.saved"], name=name, fingerprint=fp):
            continue
        out.append(Emit(feature, {"kind": "report.saved", "at": mtime_iso(path), "actor": actor,
                                  "source": "collector:archiguard",
                                  "data": {"tool": "archiguard", "name": name, "fingerprint": fp,
                                           "source_file": rel_path(path, root)}},
                        [evidence_of(path, root)]))
    return out


def escalation_emits(rc: Any, feature_dir: Path, feature: str, chain: List[Dict[str, Any]], notes: List[Path],
                     tool: str) -> List[Emit]:
    root = rc.root
    out: List[Emit] = []
    actor = rc.actor_script(tool)
    present = set()
    for path in notes:
        rel = rel_path(path, root)
        present.add(rel)
        fp = content_hash(read_bytes(path))
        prev = last(chain, ["escalation.raised", "escalation.withdrawn"], note=rel)
        if prev is not None and prev.get("kind") == "escalation.raised" and (prev.get("data") or {}).get("note_sha256") == fp:
            continue
        if prev is not None and prev.get("kind") == "escalation.raised" and last(chain, ["escalation.decided"], note=rel,
                                                                                   note_sha256=fp):
            continue
        text = read_text(path)
        gate = path.stem.replace("scopeguard-escalation-", "").replace("escalation-", "")
        out.append(Emit(feature, {"kind": "escalation.raised", "at": mtime_iso(path), "actor": actor,
                                  "source": f"collector:{tool}",
                                  "data": {"tool": tool, "note": rel, "note_sha256": fp, "gate": gate,
                                           "items": item_ids(text), "todo": text.count("TODO(agent)")}},
                        [evidence_of(path, root)]))
    # notes that disappeared (the gate passed later) are withdrawn
    for ev in every(chain, ["escalation.raised"], tool=tool):
        note = (ev.get("data") or {}).get("note")
        if not note or note in present:
            continue
        latest = last(chain, ["escalation.raised", "escalation.withdrawn"], note=note)
        if latest is ev:
            out.append(Emit(feature, {"kind": "escalation.withdrawn", "actor": actor, "source": f"collector:{tool}",
                                      "data": {"tool": tool, "note": note,
                                               "note_sha256": (ev.get("data") or {}).get("note_sha256"),
                                               "raised_event": ev.get("hash")}}))
    return out
