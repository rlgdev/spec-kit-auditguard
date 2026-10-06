"""The hook state machine (US1, US2, FR-0xx, FR-1xx) and SC-001."""

from __future__ import annotations

import json
import time

from conftest import archiguard_verdict


def test_sc001_every_command_has_one_start_and_one_end(opened):
    f = opened.feature()
    for cmd, artefact in (("plan", "plan.md"), ("tasks", "tasks.md"), ("implement", "tasks.md")):
        opened.hook(f"before_{cmd}")
        opened.append(f"{f}/{artefact}", f"\n{cmd} work\n")
        opened.hook(f"after_{cmd}")
    evs = opened.events()
    for cmd in ("plan", "tasks", "implement"):
        starts = [e for e in evs if e["kind"] == "command.started" and e["command"] == f"speckit.{cmd}"]
        ends = [e for e in evs if e["kind"] in ("command.finished", "command.abandoned") and e["command"] == f"speckit.{cmd}"]
        assert len(starts) == 1 and len(ends) == 1, cmd
        assert ends[0]["data"]["started_event"] == starts[0]["hash"]
    # a repeated after_ hook does not invent a second start
    opened.hook("after_implement")
    evs = opened.events()
    assert len([e for e in evs if e["kind"] == "command.started" and e["command"] == "speckit.implement"]) == 1
    last = [e for e in evs if e["kind"] == "command.finished"][-1]
    assert last["data"]["started_missing"] is True


def test_command_records_artefacts_and_changes(opened):
    f = opened.feature()
    opened.hook("before_plan")
    opened.write(f"{f}/plan.md", "# Plan\n")
    opened.write(f"{f}/contracts/api.yaml", "openapi: 3.0.0\n")
    opened.write("src/new.py", "x = 1\n")
    out = opened.hook("after_plan").stdout
    assert "command.finished (pass)" in out
    fin = [e for e in opened.events() if e["kind"] == "command.finished"][0]
    changed = {c["path"] for c in fin["changed"]}
    assert {f"{f}/plan.md", f"{f}/contracts/api.yaml", "src/new.py"} <= changed
    assert f"{f}/spec.md" in fin["artefacts"] and f"{f}/spec.md" not in changed
    assert fin["actor"] == {"type": "agent", "id": "claude"}
    assert fin["stage"] == "design"


def test_specify_records_the_new_feature(opened):
    opened.hook("before_specify")
    assert not (opened.root / "audit" / "sprints" / "S-1" / "001-new").exists()
    f = opened.feature("001-new")
    opened.hook("after_specify")
    kinds = opened.kinds("001-new")
    assert kinds == ["stage.entered", "command.started", "command.finished"]
    fin = opened.events("001-new")[-1]
    assert [c["path"] for c in fin["changed"]] == [f"{f}/spec.md"]


def test_stage_markers_and_milestones(opened):
    opened.feature()
    opened.hook("before_plan")
    opened.hook("after_plan")
    opened.ag("decide", "design", "approve", "--by", "Tech lead", "--role", "tech lead")
    opened.hook("before_implement")
    opened.hook("after_implement")
    opened.ag("decide", "implement", "approve", "--by", "Tech lead")
    opened.ag("decide", "pr", "approve", "--by", "Tech lead")
    kinds = opened.kinds()
    assert kinds.index("design.signed") < kinds.index("stage.completed")
    stage_events = [(e["kind"], e["data"]["stage"]) for e in opened.events() if e["kind"].startswith("stage.")]
    assert stage_events == [("stage.entered", "design"), ("stage.completed", "design"), ("stage.entered", "implement"),
                            ("stage.completed", "implement"), ("stage.entered", "test"), ("stage.completed", "test")]
    # going back to design after it completed is visible
    opened.hook("before_plan")
    assert opened.kinds()[-2:] == ["stage.reentered", "command.started"]


def test_design_reject_reopens(opened):
    opened.feature()
    opened.hook("before_plan")
    opened.hook("after_plan")
    opened.ag("decide", "design", "approve", "--by", "Tech lead")
    opened.ag("decide", "design", "reject", "--by", "Tech lead", "--reason", "contract changed")
    assert opened.kinds()[-2:] == ["design.reopened", "stage.reentered"]


def test_stop_closes_an_escalated_command_at_once(opened):
    (opened.root / ".specify/extensions/archiguard").mkdir(parents=True)   # archiGuard installed
    f = opened.feature()
    opened.hook("before_tasks")
    opened.write(f"{f}/gates/escalation-tasks-b.md", "# Escalation\n\nTODO(agent) ARCH-201\n")
    opened.event("stop", {"hook_event_name": "Stop", "session_id": "s1"})
    evs = opened.events()
    ab = [e for e in evs if e["kind"] == "command.abandoned"]
    assert len(ab) == 1 and ab[0]["outcome"] == "escalated"
    assert any(e["kind"] == "escalation.raised" for e in evs)
    assert ab[0]["data"]["outcome_file"] == "gates/escalation-tasks-b.md"


def test_stop_without_escalation_waits_for_the_next_command(opened):
    opened.feature()
    opened.hook("before_clarify")
    opened.now = "2026-10-06T09:05:00+02:00"
    opened.event("stop", {"hook_event_name": "Stop"})       # clarify asks the user - the command spans turns
    assert "command.abandoned" not in opened.kinds()
    opened.now = "2026-10-06T09:20:00+02:00"
    opened.hook("after_clarify")                             # the answer came: the same command finishes
    assert opened.kinds()[-1] == "command.finished"
    opened.hook("before_plan")
    opened.event("stop", {"hook_event_name": "Stop"})
    opened.now = "2026-10-06T11:00:00+02:00"
    opened.hook("before_tasks")                              # plan never got its after_ hook
    ab = [e for e in opened.events() if e["kind"] == "command.abandoned"][0]
    assert ab["command"] == "speckit.plan" and ab["outcome"] == "unknown"
    assert ab["at"] == "2026-10-06T09:20:00+02:00"           # the time of the stop, not of the next hook


def test_outcome_fail_when_the_step_verdict_has_violations(opened):
    f = opened.feature()
    opened.hook("before_plan")
    v = archiguard_verdict("violation", findings=1)
    opened.write(f"{f}/gates/plan-b.json", json.dumps(v))
    out = opened.hook("after_plan").stdout
    assert "(fail)" in out


def test_sessions_are_recorded_once(opened):
    for _ in range(2):
        opened.event("session_start", {"hook_event_name": "SessionStart", "session_id": "abc", "source": "startup"})
    opened.feature()
    opened.hook("before_plan")
    started = [e for e in opened.events() if e["kind"] == "command.started"][0]
    assert started["actor"]["session"] == "abc"
    opened.event("session_end", {"hook_event_name": "SessionEnd", "session_id": "abc", "reason": "exit"})
    kinds = opened.kinds("_project")
    assert kinds.count("session.started") == 1 and kinds.count("session.ended") == 1
    assert "command.abandoned" in opened.kinds()             # the open plan was closed with the session


def test_integration_switch_records_exactly_once(opened):
    opened.feature()
    out = opened.ag("hook", "before_plan", "--via", "workflow").stdout
    assert "skipped" in out
    opened.write(".specify/extensions/auditguard/auditguard-config.yml", "integration: workflow\n")
    out = opened.ag("hook", "before_plan", "--via", "hooks").stdout
    assert "skipped" in out
    assert opened.events() == []
    opened.ag("hook", "before_plan", "--via", "workflow")
    assert opened.kinds() == ["stage.entered", "command.started"]


def test_hook_never_breaks_the_command_in_record_mode(opened):
    opened.feature()
    opened.hook("before_plan")
    path = opened.root / "audit/sprints/S-1/001-place-order/journal.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("command.started", "command.STARTED"), encoding="utf-8")
    proc = opened.hook("after_plan")                      # exit 0
    assert "could not record" in proc.stdout
    proc = opened.ag("hook", "after_plan", AUDITGUARD_MODE="enforce", code=2)
    assert "could not record" in proc.stdout


def test_stop_event_never_blocks_the_agent(opened):
    opened.write("audit/sprints.yml", "this: is: not: yaml: [")
    proc = opened.event("stop", {"hook_event_name": "Stop"})
    assert proc.returncode == 0 and proc.stdout == ""


def test_hook_latency(opened):
    """SC-006: a hook stays fast on a feature with 200 events (CI measures it; the bound here is lenient)."""
    from auditguard_core.config import load_config
    from auditguard_core.store import Store
    opened.feature()
    s = Store(opened.root, load_config(opened.root))
    for i in range(200):
        s.append("001-place-order", {"kind": "note", "feature": "specs/001-place-order", "data": {"text": str(i)}})
    t0 = time.time()
    opened.hook("before_plan")
    elapsed = time.time() - t0
    assert elapsed < 6.0, f"hook took {elapsed:.2f}s"
