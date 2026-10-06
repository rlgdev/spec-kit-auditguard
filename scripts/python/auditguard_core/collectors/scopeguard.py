"""scopeGuard collector: the read-only coverage report, the iteration history and the escalation notes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common import content_hash, read_bytes, read_json, rel_path, sha256_json
from .archiguard import escalation_emits
from .base import Emit, evidence_of, last, mtime_iso

PHASE_ARTEFACT = {"plan": "plan.md", "tasks": "tasks.md", "implement": "tasks.md"}


def engine(root: Path) -> Optional[Path]:
    path = root / ".specify" / "extensions" / "scopeguard" / "scripts" / "python" / "scopeguard.py"
    return path if path.is_file() else None


def run_report(root: Path, feature_dir: Path, timeout: int) -> Optional[Dict[str, Any]]:
    script = engine(root)
    if script is None:
        return None
    try:
        proc = subprocess.run([sys.executable, str(script), "report", "--json", "--root", str(root),
                               "--feature-dir", rel_path(feature_dir, root)],
                              cwd=str(root), capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def collect(rc: Any, feature_dir: Path, feature: str, chain: List[Dict[str, Any]]) -> List[Emit]:
    root = rc.root
    out: List[Emit] = []
    actor = rc.actor_script("scopeguard")
    conf = rc.cfg.collector("scopeguard")
    report = run_report(root, feature_dir, int(conf.get("timeout") or 120)) if conf.get("report", True) else None
    feat = None
    if report:
        for f in report.get("features") or []:
            if Path(str(f.get("feature_dir"))).name == feature_dir.name:
                feat = f
                break
    if feat is None and report is not None and not engine(root):
        return out
    summaries = (feat or {}).get("summary") or {}
    report_bytes = json.dumps(feat, indent=2, ensure_ascii=False).encode("utf-8") if feat else None
    for phase in ("plan", "tasks", "implement"):
        if not (feature_dir / PHASE_ARTEFACT[phase]).is_file():
            continue
        summary = summaries.get(phase)
        hist_path = feature_dir / ".scopeguard" / f"history-{phase}.json"
        history = read_json(hist_path, {}) or {}
        iterations = history.get("iterations") or []
        if summary is None and not iterations:
            continue
        fp = sha256_json({"summary": summary, "history": iterations})
        prev = last(chain, ["gate.verdict"], tool="scopeguard", gate=phase)
        if prev is not None and (prev.get("data") or {}).get("fingerprint") == fp:
            continue
        s = summary or {}
        violations = int(s.get("violations") or 0)
        in_scope = s.get("in_scope")
        covered = int(s.get("pass") or 0)
        status = ("fail" if violations else "pass") if summary is not None else (iterations[-1].get("verdict") if iterations else None)
        evidence = []
        if report_bytes is not None:
            evidence.append(("scopeguard-report.json", report_bytes, None))
        if hist_path.is_file():
            evidence.append(evidence_of(hist_path, root))
        last_iter = iterations[-1] if iterations else {}
        out.append(Emit(feature, {
            "kind": "gate.verdict", "at": mtime_iso(hist_path) if hist_path.is_file() and not summary else None,
            "actor": actor, "source": "collector:scopeguard",
            "data": {"tool": "scopeguard", "gate": phase, "status": status,
                     "coverage": f"{covered}/{in_scope}" if in_scope is not None else None,
                     "coverage_pct": s.get("coverage_pct"), "pass": s.get("pass"), "violations": violations,
                     "waived": s.get("waived"), "warnings": s.get("warnings"),
                     "iterations": len(iterations) if iterations else None,
                     "last_iteration": {"iteration": last_iter.get("iteration"), "verdict": last_iter.get("verdict"),
                                        "open": last_iter.get("open")} if last_iter else None,
                     "open_items": [r.get("id") for r in (feat or {}).get("rows") or []
                                    if (r.get(phase) or {}).get("verdict") == "violation"][:40],
                     "fingerprint": fp}}, evidence))
    notes = sorted(feature_dir.glob("scopeguard-escalation-*.md"))
    out += escalation_emits(rc, feature_dir, feature, chain, notes, "scopeguard")
    saved = feature_dir / "scopeguard-report.md"
    if saved.is_file():
        fp = content_hash(read_bytes(saved))
        if not last(chain, ["report.saved"], name="scopeguard-report.md", fingerprint=fp):
            out.append(Emit(feature, {"kind": "report.saved", "at": mtime_iso(saved), "actor": actor,
                                      "source": "collector:scopeguard",
                                      "data": {"tool": "scopeguard", "name": "scopeguard-report.md", "fingerprint": fp,
                                               "source_file": rel_path(saved, root)}},
                            [evidence_of(saved, root)]))
    return out
