"""Collectors (FR-4xx): waiver history (SC-009), archiGuard evidence, the decision ledger, workflow runs, plug-ins,
idempotence - and the real scopeGuard engine when SCOPEGUARD_SRC points at a checkout."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from auditguard_core.common import sha256_text

from conftest import archiguard_verdict

PLAN = """# Plan

## Scope Coverage

| ID | Title | Status | Plan reference | Reason (required if deferred) |
|----|-------|--------|----------------|-------------------------------|
| US1 | Place an order | covered | contracts/orders.yaml | |
| FR-001 | Order total | covered | data-model.md | |
{fr2}
"""


def plan_run(p, f, row):
    p.hook("before_plan")
    p.write(f"{f}/plan.md", PLAN.format(fr2=row))
    p.hook("after_plan")


def test_sc009_waiver_added_changed_removed(opened):
    f = opened.feature()
    plan_run(opened, f, "| FR-002 | Refunds | deferred | | PO decision: phase 2 |")
    plan_run(opened, f, "| FR-002 | Refunds | deferred | | PO decision 2026-10-10: phase 3 |")
    plan_run(opened, f, "| FR-002 | Refunds | covered | contracts/refunds.yaml | |")
    w = [e for e in opened.events() if e["kind"].startswith("waiver.")]
    assert [e["kind"] for e in w] == ["waiver.added", "waiver.changed", "waiver.removed"]
    assert w[0]["data"]["reason"] == "PO decision: phase 2"
    assert w[1]["data"]["before"] == {"reason": "PO decision: phase 2"}
    assert w[1]["data"]["after"] == {"reason": "PO decision 2026-10-10: phase 3"}
    assert w[0]["data"]["id"] == "defer:FR-002"


def test_conformance_deviation_is_a_waiver(opened):
    f = opened.feature()
    opened.hook("before_plan")
    opened.write(f"{f}/plan.md", "# Plan\n\n## Architecture Conformance\n\n| Rule | Title | Status | Plan reference | ADR / reason |\n"
                 "|---|---|---|---|---|\n| ARCH-501 | Ownership | deviation | data-model.md | ADR-0007 read model |\n"
                 "| ARCH-101 | OpenAPI | satisfied | contracts | |\n")
    opened.hook("after_plan")
    w = [e for e in opened.events() if e["kind"] == "waiver.added"][0]
    assert w["data"]["id"] == "conf:ARCH-501" and w["data"]["kind"] == "deviation"
    assert w["data"]["record"]["adr"] == ["ADR-0007"]


def ledger_line(entry, prev):
    body = dict(entry, prev=prev)
    body.pop("hash", None)
    body["hash"] = sha256_text(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return body


def write_ledger(p, entries):
    lines, prev = [], "0" * 64
    for i, e in enumerate(entries, start=1):
        line = ledger_line(dict(e, seq=i), prev)
        prev = line["hash"]
        lines.append(json.dumps(line))
    p.write(".specify/archiguard/ledger.jsonl", "\n".join(lines) + "\n")


def test_ledger_waiver_history_and_adr_decisions(opened):
    (opened.root / ".specify/extensions/archiguard").mkdir(parents=True)
    opened.feature()
    waiver = {"id": "WVR-0001", "type": "waiver", "status": "proposed", "title": "keep sandbox key", "rules": ["ARCH-401"],
              "owner": "Orders", "approver": None, "created": "2026-10-01", "expires": "2026-10-03",
              "scope": {"features": ["001-place-order"], "contexts": []}}
    adr = {"id": "ADR-0007", "type": "adr", "status": "approved", "title": "read model", "rules": ["ARCH-501"],
           "owner": "Orders", "approver": "Lead architect", "created": "2026-10-01", "expires": "2027-06-30"}
    write_ledger(opened, [waiver, adr])
    opened.ag("collect")
    write_ledger(opened, [waiver, adr, dict(waiver, status="approved", approver="Lead architect")])
    opened.ag("collect")
    kinds = [e["kind"] for e in opened.events() if e["kind"].startswith("waiver.")]
    assert kinds == ["waiver.added", "waiver.expired", "waiver.approved"]
    assert opened.events()[-1]["data"]["approver"] == "Lead architect"
    decisions = [e for e in opened.events("_project") if e["kind"] == "decision"]
    assert decisions and decisions[0]["data"]["subject"] == "ledger:ADR-0007"
    assert decisions[0]["data"]["verdict"] == "approved"


def test_archiguard_verdicts_signoff_and_reopen(opened):
    (opened.root / ".specify/extensions/archiguard").mkdir(parents=True)
    f = opened.feature()
    opened.hook("before_plan")
    v = archiguard_verdict("pass")
    opened.write(f"{f}/gates/plan-b.json", json.dumps(v))
    opened.write(f"{f}/gates/A3/A3.3.json", json.dumps({"status": "pass"}))
    opened.hook("after_plan")
    gv = [e for e in opened.events() if e["kind"] == "gate.verdict"]
    assert len(gv) == 1 and gv[0]["data"]["step"] == "plan-b" and gv[0]["data"]["iterations"] == 1
    assert {r["name"] for r in gv[0]["evidence"]} == {"plan-b.json", "A3.3.json"}
    fin = opened.events()[-1]
    assert fin["data"]["gates"][0]["step"] == "plan-b"
    # idempotent
    assert "0 event(s)" in opened.ag("collect").stdout
    signoff = {"by": "Tech lead", "role": "tech lead", "at": "2026-10-08T14:00:00+02:00", "feature": f, "commit": None,
               "hashes": {"spec.md": "x"}, "pins": {}, "evidence": {"speckit.plan": "pass"}}
    opened.write(f"{f}/gates/signoff.json", json.dumps(signoff))
    opened.ag("collect")
    kinds = opened.kinds()
    assert kinds[-3:] == ["decision", "design.signed", "stage.completed"]
    assert opened.events()[-2]["at"].startswith("2026-10-08T14:00:00")
    # archiguard reopen moves the record into signoff-history with `reopened`
    (opened.root / f / "gates/signoff.json").unlink()
    opened.write(f"{f}/gates/signoff-history/signoff-20261009T100000.json",
                 json.dumps(dict(signoff, reopened={"by": "Tech lead", "reason": "contract change", "at": "2026-10-09T10:00:00+02:00"})))
    opened.ag("collect")
    assert opened.kinds()[-3:] == ["decision", "design.reopened", "stage.reentered"]
    assert opened.kinds().count("design.signed") == 1


def test_workflow_gate_verdict_is_a_decision(opened):
    f = opened.feature()
    run = opened.root / ".specify/workflows/runs/r1"
    run.mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({"run_id": "r1", "workflow_id": "archiguard-sdd", "step_results": {
        "design-signoff": {"type": "gate", "status": "completed", "output": {"choice": "approve", "message": "sign"}},
        "plan": {"type": "command", "status": "completed", "output": {}}}}), encoding="utf-8")
    (run / "inputs.json").write_text(json.dumps({"inputs": {"feature": f, "design_authority": "Tech lead"}}), encoding="utf-8")
    opened.ag("collect")
    opened.ag("collect")
    d = [e for e in opened.events() if e["kind"] == "decision"]
    assert len(d) == 1
    assert d[0]["data"]["subject"] == "gate:design-signoff" and d[0]["data"]["by"] == "Tech lead"


def test_plugin_collector(opened):
    opened.feature()
    opened.write("tools/jira.py", "import json, sys\nprint(json.dumps([{'kind': 'issue.created', 'data': {'key': 'ORD-12'}, "
                 "'evidence': [{'name': 'issue.json', 'path': 'tools/issue.json'}]}]))\n")
    opened.write("tools/issue.json", '{"key": "ORD-12"}\n')
    opened.write(".specify/extensions/auditguard/auditguard-config.yml", "collectors:\n  jira:\n    command: tools/jira.py\n")
    opened.ag("collect")
    evs = [e for e in opened.events() if e["kind"].startswith("jira.")]
    assert evs and evs[0]["kind"] == "jira.issue.created" and evs[0]["evidence"][0]["name"] == "issue.json"


def test_failing_plugin_is_recorded_not_fatal(opened):
    opened.feature()
    opened.write("tools/bad.py", "import sys\nsys.exit('boom')\n")
    opened.write(".specify/extensions/auditguard/auditguard-config.yml", "collectors:\n  bad:\n    command: tools/bad.py\n")
    opened.ag("collect")
    assert any(e["kind"] == "collector.error" for e in opened.events())


SG = os.environ.get("SCOPEGUARD_SRC")


@pytest.mark.skipif(not SG, reason="set SCOPEGUARD_SRC to a spec-kit-scopeguard checkout")
def test_real_scopeguard_report(opened):
    ext = opened.root / ".specify/extensions/scopeguard"
    shutil.copytree(Path(SG) / "scripts", ext / "scripts")
    shutil.copy(Path(SG) / "extension.yml", ext / "extension.yml")
    f = opened.feature(spec="# Spec\n\n### User Story 1 - Place an order (Priority: P1)\n\n- **FR-001**: total\n- **FR-002**: refunds\n")
    plan_run(opened, f, "| FR-002 | Refunds | deferred | | PO decision: phase 2 |")
    gv = [e for e in opened.events() if e["kind"] == "gate.verdict" and e["data"]["tool"] == "scopeguard"]
    assert gv and gv[0]["data"]["gate"] == "plan" and gv[0]["data"]["status"] == "pass"
    assert gv[0]["data"]["waived"] == 1
    assert any(r["name"] == "scopeguard-report.json" for r in gv[0]["evidence"])
