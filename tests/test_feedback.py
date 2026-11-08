"""Loop feedback (FR-4xx / FR-5xx of archiGuard's loop-feedback specification): the collector records archiGuard's
gates/loop-feedback.json as feedback.raised / updated / resolved, a person triages with decide feedback:<id>, and
`auditguard backlog` hands the items over - merged across features, sealed with the sprint."""

from __future__ import annotations

import csv
import io
import json
from typing import List, Optional

import pytest

from auditguard_core import feedback as fb

F1 = "specs/001-place-order"
F2 = "specs/002-refunds"


def item(fid: str = "LF-2a2a5a3d", kind: str = "new-unit", subject: str = "acme-shop:services/notifications",
         impact: str = "high", suggestion: str = "Declare it as a deployable, or move its code into the existing unit '.'",
         files: Optional[List[str]] = None) -> dict:
    files = files if files is not None else ["services/notifications/pom.xml"]
    return {"id": fid, "kind": kind, "subject": subject,
            "title": f"{kind} {subject}", "observation": f"Observed {subject}.", "suggestion": suggestion,
            "for": [{"role": "lead architect"}], "target": "domain-map: deployables", "impact": impact, "waiver": None,
            "occurrences": {"count": len(files), "items": files}, "evidence": [{"path": f} for f in files],
            "first_seen": "2026-10-07T10:00:00+02:00"}


GAP = item("LF-c90bb238", "map-gap", "customers:no-code", "low", "Declare the code paths of customers.", [])


def feedback_file(items: List[dict], status: str = "evaluated", resolved: Optional[List[dict]] = None,
                  feature: str = F1, at: str = "2026-10-07T10:00:00+02:00", reason: Optional[str] = None) -> str:
    doc = {"tool": "archiguard", "kind": "loop-feedback", "schema": 1, "guardrail_version": "0.2.0", "feature": feature,
           "status": status, "computed_at": at, "computed_by": "implement-b", "commit": None, "base": "main",
           "repository": "acme-shop", "pins": {"domain_map": "1.4.0"}, "home_context": "orders",
           "topology": {"answer": "mixed", "units": []},
           "summary": {"items": len(items)}, "items": items, "resolved": resolved or [], "skipped": [], "truncated": 0}
    if reason:
        doc["reason"] = reason
    return json.dumps(doc, indent=2)


@pytest.fixture
def ag_project(opened):
    (opened.root / ".specify/extensions/archiguard").mkdir(parents=True)
    opened.feature()
    return opened


def write(p, items, feature=F1, **kw):
    p.write(f"{feature}/gates/loop-feedback.json", feedback_file(items, feature=feature, **kw))


def fb_events(p, key="001-place-order"):
    return [e for e in p.events(key) if e["kind"].startswith("feedback.")]


# ------------------------------------------------------------------ collector


def test_raised_updated_resolved_from_successive_files(ag_project):
    p = ag_project
    write(p, [item(), GAP])
    p.ag("collect")
    evs = fb_events(p)
    assert [e["kind"] for e in evs] == ["feedback.raised", "feedback.raised"]
    raised = {e["data"]["id"]: e["data"] for e in evs}
    d = raised["LF-2a2a5a3d"]
    assert d["kind"] == "new-unit" and d["subject"] == "acme-shop:services/notifications" and d["impact"] == "high"
    assert d["occurrences"] == 1 and d["map_version"] == "1.4.0" and d["computed_by"] == "implement-b"
    assert d["home_context"] == "orders" and d["fingerprint"] == fb.item_fingerprint(item())
    saved = [e for e in p.events() if e["kind"] == "report.saved" and e["data"]["name"] == "loop-feedback.json"]
    assert len(saved) == 1 and saved[0]["evidence"][0]["name"] == "loop-feedback.json"
    assert saved[0]["data"]["items"] == 2 and saved[0]["data"]["answer"] == "mixed"
    # an unchanged file records nothing
    assert "0 event(s)" in p.ag("collect").stdout
    # a new first_seen alone is not a change of the item; a new suggestion is
    write(p, [dict(item(), first_seen="2026-10-08T10:00:00+02:00"), GAP], at="2026-10-08T10:00:00+02:00")
    p.ag("collect")
    assert [e["kind"] for e in fb_events(p)][2:] == []
    write(p, [item(suggestion="Merge it into orders."), GAP], at="2026-10-09T10:00:00+02:00")
    p.ag("collect")
    upd = fb_events(p)[-1]
    assert upd["kind"] == "feedback.updated" and upd["data"]["changed_fields"] == ["suggestion"]
    # gone from an evaluated file: resolved
    write(p, [item(suggestion="Merge it into orders.")], at="2026-10-10T10:00:00+02:00")
    p.ag("collect")
    res = fb_events(p)[-1]
    assert res["kind"] == "feedback.resolved" and res["data"]["id"] == "LF-c90bb238"
    assert res["data"]["raised_event"] == [e for e in p.events() if e["kind"] == "feedback.raised"][1]["hash"]


def test_not_evaluated_and_partial(ag_project):
    p = ag_project
    write(p, [item(), GAP])
    p.ag("collect")
    write(p, [], status="not-evaluated", reason="no domain map", at="2026-10-08T10:00:00+02:00")
    p.ag("collect")
    assert [e["kind"] for e in fb_events(p)] == ["feedback.raised", "feedback.raised"]
    saved = [e for e in p.events() if e["kind"] == "report.saved"][-1]
    assert saved["data"]["status"] == "not-evaluated" and saved["data"]["reason"] == "no domain map"
    # partial: an absent item stays open unless the file lists it as resolved
    write(p, [], status="partial", resolved=[{"id": "LF-c90bb238", "kind": "map-gap", "subject": "customers:no-code"}],
          at="2026-10-09T10:00:00+02:00")
    p.ag("collect")
    kinds = [(e["kind"], e["data"]["id"]) for e in fb_events(p)]
    assert kinds[-1] == ("feedback.resolved", "LF-c90bb238") and ("feedback.resolved", "LF-2a2a5a3d") not in kinds


def test_collector_switch_off(ag_project):
    p = ag_project
    p.config("collectors:\n  archiguard:\n    feedback: false\n")
    write(p, [item()])
    p.ag("collect")
    assert not fb_events(p)
    p.write(".specify/extensions/archiguard/extension.yml", 'extension:\n  id: archiguard\n  version: "0.2.0"\n')
    assert "loop feedback not recorded" in p.ag("configure", "--dry-run").stdout
    assert json.loads(p.ag("configure", "--dry-run", "--json").stdout)["feedback_recorded"] is False


# --------------------------------------------------------------------- triage


def project_events(p):
    return p.events("_project")


def test_decide_in_project_chain_then_ref_and_feature_chain(ag_project):
    p = ag_project
    write(p, [item(), GAP])
    p.ag("collect")
    out = p.ag("decide", "feedback:LF-2a2a5a3d", "accept", "--by", "Lead architect", "--reason", "DMD 1.5.0").stdout
    assert "feedback:LF-2a2a5a3d -> accept" in out
    dec = [e for e in project_events(p) if e["kind"] == "decision"][-1]
    assert dec["data"]["subject"] == "feedback:LF-2a2a5a3d" and dec["data"]["item"]["kind"] == "new-unit"
    assert dec["data"]["features"] == [F1] and dec["data"]["status_before"] == "new" and "ref" not in dec["data"]
    rows = json.loads(p.ag("backlog", "--status", "all", "--format", "json").stdout)
    assert {r["id"]: r["status"] for r in rows} == {"LF-2a2a5a3d": "accepted", "LF-c90bb238": "new"}
    p.ag("decide", "feedback:LF-2a2a5a3d", "accept", "--ref", "ARCH-77", "--by", "Lead architect")
    rows = json.loads(p.ag("backlog", "--status", "all", "--format", "json").stdout)
    r = {r["id"]: r for r in rows}["LF-2a2a5a3d"]
    assert r["status"] == "in-backlog" and r["ref"] == "ARCH-77" and len(r["decisions"]) == 2
    # with --feature-dir the decision goes into the feature's chain
    p.ag("decide", "feedback:LF-c90bb238", "defer", "--by", "Lead architect", "--reason", "next sprint", "--feature-dir", F1)
    assert [e for e in p.events() if e["kind"] == "decision"][-1]["data"]["verdict"] == "defer"
    rows = json.loads(p.ag("backlog", "--status", "deferred", "--format", "json").stdout)
    assert [r["id"] for r in rows] == ["LF-c90bb238"]


def test_decide_refusals(ag_project):
    p = ag_project
    write(p, [item()])
    p.ag("collect")
    assert "--reason is required" in p.ag("decide", "feedback:LF-2a2a5a3d", "reject", "--by", "x", code=2).stderr
    assert "--reason is required" in p.ag("decide", "feedback:LF-2a2a5a3d", "defer", "--by", "x", code=2).stderr
    assert "no loop-feedback item LF-00000000" in p.ag("decide", "feedback:LF-00000000", "accept", "--by", "x", code=2).stderr
    assert "name the feedback" in p.ag("decide", "feedback", "accept", "--by", "x", code=2).stderr
    assert "--ref" in p.ag("decide", "pr", "approve", "--ref", "X-1", "--by", "x", code=2).stderr
    assert "not a verdict" in p.ag("decide", "feedback:LF-2a2a5a3d", "approve", "--by", "x", code=2).stderr
    proc = p.ag("decide", "feedback:LF-2a2a5a3d", "accept", "--by", "x", AUDITGUARD_CONTEXT="agent", code=3)
    assert "agent context" in proc.stderr
    # the backlog is read-only and allowed for agents
    p.ag("backlog", AUDITGUARD_CONTEXT="agent")


def ev(kind, fid, feature="specs/001-place-order", at="2026-10-07T10:00:00+02:00", seq=1, **data):
    return {"kind": kind, "feature": feature, "at": at, "seq": seq, "sprint": "S-1", "_sprint_dir": "S-1",
            "data": dict({"id": fid, "kind": "new-unit", "subject": "s"}, **data)}


def decision(fid, verdict, ref=None, at="2026-10-08T10:00:00+02:00", seq=1):
    data = {"subject": f"feedback:{fid}", "verdict": verdict}
    if ref:
        data["ref"] = ref
    return {"kind": "decision", "feature": None, "at": at, "seq": seq, "sprint": "S-1", "_sprint_dir": "S-1", "data": data}


@pytest.mark.parametrize("decisions, still_open, status", [
    ([], True, "new"),
    ([("defer", None)], True, "deferred"),
    ([("accept", None)], True, "accepted"),
    ([("accept", "ARCH-77")], True, "in-backlog"),
    ([("reject", None)], True, "rejected"),
    ([], False, "resolved"),
    ([("reject", None)], False, "resolved"),
    ([("defer", None)], False, "resolved"),
    ([("accept", None)], False, "accepted"),
    ([("accept", "ARCH-77")], False, "in-backlog"),
    ([("reject", None), ("accept", None)], True, "accepted"),
    ([("accept", None), ("defer", None)], True, "deferred"),
])
def test_triage_status_table(decisions, still_open, status):
    chain = [ev("feedback.raised", "LF-1", seq=1)]
    if not still_open:
        chain.append(ev("feedback.resolved", "LF-1", at="2026-10-09T10:00:00+02:00", seq=2))
    project = [decision("LF-1", v, r, at=f"2026-10-08T1{i}:00:00+02:00", seq=i + 1) for i, (v, r) in enumerate(decisions)]
    rec = fb.items({"001-place-order": chain, "_project": project})["LF-1"]
    assert rec["status"] == status


def test_known_item_in_a_second_feature(ag_project):
    p = ag_project
    write(p, [GAP])
    p.ag("collect")
    p.ag("decide", "feedback:LF-c90bb238", "reject", "--by", "Lead architect", "--reason", "customers has no code yet")
    p.feature("002-refunds")
    write(p, [GAP], feature=F2, at="2026-10-09T10:00:00+02:00")
    p.ag("collect", "--feature-dir", F2)
    raised = fb_events(p, "002-refunds")[0]
    assert raised["data"]["known"]["verdict"] == "reject" and raised["data"]["known"]["sprint"] == "S-1"
    rows = json.loads(p.ag("backlog", "--status", "all", "--format", "json").stdout)
    assert rows[0]["status"] == "rejected" and rows[0]["features"] == [F1, F2]
    # a rejected item is not an open item
    assert "loop-feedback" not in p.ag("show", "--all", "--open").stdout


# -------------------------------------------------------------------- backlog


def test_backlog_merges_features_and_prints_tracker_csv(ag_project):
    p = ag_project
    write(p, [item(), GAP])
    p.ag("collect")
    p.feature("002-refunds")
    write(p, [item()], feature=F2, at="2026-10-08T10:00:00+02:00")
    p.ag("collect", "--feature-dir", F2)
    text = p.ag("backlog", "--format", "csv").stdout
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["External ID", "Summary", "Description", "Priority", "Labels", "Status", "Ref", "Features",
                       "First sprint", "Evidence"]
    assert [r[0] for r in rows[1:]] == ["LF-2a2a5a3d", "LF-c90bb238"]          # sorted by impact
    r = rows[1]
    assert r[1] == "new-unit acme-shop:services/notifications" and r[3] == "High" and r[5] == "new"
    assert r[4] == "loop-feedback;new-unit;orders" and r[7] == f"{F1};{F2}" and r[8] == "S-1"
    assert "Suggestion: Declare it as a deployable" in r[2] and "For: lead architect." in r[2]
    assert r[9].startswith("audit/sprints/S-1/") and r[9].endswith("-loop-feedback.json")
    assert (p.root / r[9]).is_file()
    assert rows[2][4] == "loop-feedback;map-gap;customers;orders" and rows[2][3] == "Low"
    # Markdown: one table per addressee; --out writes the file; the status filter
    md = p.ag("backlog").stdout
    assert "## For: lead architect" in md and "| LF-2a2a5a3d | high | new-unit |" in md
    p.ag("backlog", "--status", "accepted", "--format", "csv", "--out", "backlog.csv")
    assert (p.root / "backlog.csv").read_text(encoding="utf-8").strip().count("\n") == 0
    assert "unknown status" in p.ag("backlog", "--status", "open", code=2).stderr
    # the backlog records nothing
    before = len(p.events()) + len(p.events("002-refunds"))
    p.ag("backlog", "--all")
    assert len(p.events()) + len(p.events("002-refunds")) == before


def test_backlog_config_priority_and_labels(ag_project):
    p = ag_project
    p.config("backlog:\n  priority: { high: P1, medium: P2, low: P3 }\n  labels: [architecture, dmd]\n")
    write(p, [item()])
    p.ag("collect")
    rows = list(csv.reader(io.StringIO(p.ag("backlog", "--format", "csv").stdout)))
    assert rows[1][3] == "P1" and rows[1][4] == "architecture;dmd;new-unit;orders"


# ----------------------------------------------------------- views and sprints


def test_views_show_the_items(ag_project):
    p = ag_project
    write(p, [item(), GAP])
    p.ag("collect")
    trail = (p.root / "audit/sprints/S-1/001-place-order/trail.md").read_text(encoding="utf-8")
    assert "## Loop feedback" in trail and "| LF-2a2a5a3d | high | new-unit |" in trail
    assert "2 loop-feedback item(s) untriaged" in trail
    sprint = (p.root / "audit/sprints/S-1/sprint.md").read_text(encoding="utf-8")
    assert "## Loop feedback to the domain master design" in sprint and "001-place-order" in sprint
    feature = (p.root / "audit/features/001-place-order.md").read_text(encoding="utf-8")
    assert "## Loop feedback" in feature and "LF-c90bb238" in feature
    view = (p.root / "audit/sprints/S-1/loop-feedback.md").read_text(encoding="utf-8")
    assert view.startswith("# Loop feedback — Sprint S-1") and "LF-2a2a5a3d" in view
    shown = p.ag("show", "--kind", "feedback").stdout
    assert "loop feedback raised" in shown
    assert "[feedback] LF-2a2a5a3d new: new-unit" in p.ag("show", "--open").stdout


def test_views_without_feedback_have_no_section(opened):
    opened.feature()
    opened.hook("before_plan")
    opened.hook("after_plan")
    opened.ag("render")
    trail = (opened.root / "audit/sprints/S-1/001-place-order/trail.md").read_text(encoding="utf-8")
    assert "Loop feedback" not in trail
    assert not (opened.root / "audit/sprints/S-1/loop-feedback.md").exists()


def test_seal_counts_the_triage_inside_the_hash(ag_project):
    p = ag_project
    write(p, [item(), GAP])
    p.ag("collect")
    p.ag("decide", "feedback:LF-2a2a5a3d", "accept", "--by", "Lead architect", "--ref", "ARCH-77")
    p.ag("sprint", "close", "--by", "Roman")
    seal = json.loads((p.root / "audit/sprints/S-1/seal.json").read_text(encoding="utf-8"))
    assert seal["feedback"] == {"new": 1, "deferred": 0, "accepted": 0, "in_backlog": 1, "rejected": 0, "resolved": 0}
    from auditguard_core.common import sha256_json
    assert seal["hash"] == sha256_json({k: v for k, v in seal.items() if k != "hash"})
    assert p.ag("verify").returncode == 0
    assert (p.root / "audit/sprints/S-1/loop-feedback.md").is_file()


def test_rule_is_opt_in_warn_in_record_fail_in_enforce(ag_project):
    p = ag_project
    write(p, [item()])
    p.ag("collect")
    assert "feedback_triaged_before_close" not in p.ag("check").stdout
    p.config("rules:\n  feedback_triaged_before_close: true\n")
    assert "[WARN] feedback_triaged_before_close" in p.ag("check").stdout
    proc = p.ag("sprint", "close", "--by", "Roman", AUDITGUARD_MODE="enforce", code=1)
    assert "feedback_triaged_before_close" in proc.stderr
    p.ag("decide", "feedback:LF-2a2a5a3d", "defer", "--by", "Lead architect", "--reason", "next sprint")
    assert "[OK]   feedback_triaged_before_close" in p.ag("check").stdout
