"""Spec Kit workflow collector: the verdict of every `type: gate` step of a workflow run is a human decision."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common import read_json, read_text, rel_path, sha256_json
from .base import Emit, every

PERSON_INPUTS = ("design_authority", "approver", "reviewer", "tech_lead", "qa_lead", "by", "decided_by")


def _feature_of(inputs: Dict[str, Any], root: Path) -> Optional[str]:
    value = inputs.get("feature") or inputs.get("feature_dir")
    if not value:
        return None
    p = Path(str(value))
    if not p.is_absolute():
        p = root / p
    return rel_path(p, root) if p.is_dir() else None


def _completed_at(run_dir: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    log = run_dir / "log.jsonl"
    if not log.is_file():
        return out
    for raw in read_text(log).split("\n"):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("event") == "step_completed" and entry.get("step_id"):
            out[str(entry["step_id"])] = str(entry.get("timestamp") or "")
    return out


def collect(rc: Any, chains: Dict[Optional[str], List[Dict[str, Any]]], loader: Any) -> List[Emit]:
    """`chains` maps a feature (or None) to its chain; `loader(feature)` loads one that is not there yet."""
    root = rc.root
    runs_dir = rc.cfg.path(rc.cfg.collector("workflow").get("runs"))
    out: List[Emit] = []
    if runs_dir is None or not runs_dir.is_dir():
        return out
    for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        state = read_json(run_dir / "state.json", {}) or {}
        inputs = (read_json(run_dir / "inputs.json", {}) or {}).get("inputs") or {}
        results = state.get("step_results") or {}
        if not isinstance(results, dict):
            continue
        feature = _feature_of(inputs, root)
        if feature not in chains:
            chains[feature] = loader(feature)
        chain = chains[feature]
        done_at = _completed_at(run_dir)
        person = next((str(inputs[k]) for k in PERSON_INPUTS if inputs.get(k)), None)
        for step_id, res in results.items():
            if not isinstance(res, dict) or res.get("type") != "gate":
                continue
            output = res.get("output") or {}
            choice = output.get("choice")
            if not choice:
                continue
            subject = f"gate:{step_id}"
            fp = sha256_json({"run": state.get("run_id"), "step": step_id, "choice": choice, "status": res.get("status")})
            if any((ev.get("data") or {}).get("fingerprint") == fp for ev in every(chain, ["decision"], subject=subject)):
                continue
            data = {"subject": subject, "verdict": choice, "run_id": state.get("run_id"),
                    "workflow_id": state.get("workflow_id"), "message": output.get("message"),
                    "status": res.get("status"), "aborted": bool(output.get("aborted")), "fingerprint": fp}
            if person:
                data["by"] = person
                data["confidence"] = "workflow input"
            else:
                data["by"] = rc.actor_human(None).get("id")
                data["confidence"] = "inferred"
            out.append(Emit(feature, {"kind": "decision", "at": done_at.get(step_id) or state.get("updated_at"),
                                      "actor": rc.actor_script("speckit-workflow"), "source": "collector:workflow",
                                      "data": data}))
    return out
