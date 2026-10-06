"""Integration: the two-sprint example with the real scopeGuard and archiGuard engines verifies end to end.

Runs when ARCHIGUARD_SRC and SCOPEGUARD_SRC point at checkouts of the sibling repositories (CI clones them)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import REPO

AG = os.environ.get("ARCHIGUARD_SRC")
SG = os.environ.get("SCOPEGUARD_SRC")


@pytest.mark.skipif(not (AG and SG), reason="set ARCHIGUARD_SRC and SCOPEGUARD_SRC to sibling checkouts")
def test_example_story_verifies(tmp_path):
    out = tmp_path / "orders"
    repo = tmp_path / "repo"
    proc = subprocess.run([sys.executable, str(REPO / "tools/make-example.py"), "--archiguard-src", AG, "--scopeguard-src", SG,
                           "--out", str(out), "--keep-repo", str(repo)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    engine = repo / ".specify/extensions/auditguard/scripts/python/auditguard.py"
    proc = subprocess.run([sys.executable, str(engine), "verify", "--golden", "--recompute", "--offline"], cwd=repo,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads((repo / "audit/verify-report.json").read_text(encoding="utf-8"))
    checks = report["golden"]["checks"]
    assert checks["G3"]["unexplained"] == 0 and checks["G6"] == {"verified": 1}
    assert checks["G8"].get("verified", 0) >= 1 and not checks["G8"].get("mismatch")
    kinds = [json.loads(l)["kind"] for p in (repo / "audit/sprints").glob("*/001-place-order/journal.jsonl")
             for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    for kind in ("escalation.raised", "escalation.decided", "design.signed", "waiver.added", "artefact.changed",
                 "implement.approved", "pr.approved", "gate.verdict"):
        assert kind in kinds, kind
    assert (out / "audit/viewer/index.html").is_file() and not (out / ".git").exists()
