"""The profiles: `light` (the default) records the commands and runs nothing else in the agent's loop; `full` is
the complete recorder. `configure` prunes the switched-off agent events from the agent's native event config."""

from __future__ import annotations

import json

import pytest

from auditguard_core.config import PROFILE_KEYS, AuditGuardError, load_config
from auditguard_core.configure import prune_native_events
from conftest import REPO

TEMPLATE = (REPO / "config-template.yml").read_text(encoding="utf-8")


def dispatcher(command: str) -> str:
    return f'python3 "${{CLAUDE_PROJECT_DIR}}/.specify/events.py" {command} <event> 30 plain'


def settings(*entries: str, user_hook: bool = True) -> dict:
    """A `.claude/settings.json` the way Spec Kit writes it: one entry per handler, grouped by matcher."""
    data = {"permissions": {"allow": ["Bash(git:*)"]}, "hooks": {
        "SessionStart": [{"matcher": "*", "hooks": [{"type": "command", "command": dispatcher("speckit.auditguard.sessionstart"),
                                                       "timeout": 20, "__speckit_event__": True}]}],
        "Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": dispatcher("speckit.auditguard.stop"), "timeout": 35,
                                               "__speckit_event__": True}]}],
        "SessionEnd": [{"matcher": "*", "hooks": [{"type": "command", "command": dispatcher("speckit.auditguard.sessionend"),
                                                     "timeout": 20, "__speckit_event__": True}]}],
        "PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell", "hooks": [
            {"type": "command", "command": dispatcher("speckit.archiguard.guard"), "timeout": 15, "__speckit_event__": True},
            {"type": "command", "command": dispatcher("speckit.auditguard.guard"), "timeout": 15, "__speckit_event__": True}]}],
    }}
    if user_hook:
        data["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [{"type": "command", "command": "./my-own-check.sh"}]})
    return data


# ------------------------------------------------------------------ config


def test_light_is_the_default_and_the_template_says_so(project):
    cfg = load_config(project.root)
    assert cfg.profile == "light"
    assert cfg.switches() == {"render.on_hook": False, "sessions.record": False, "events.stop": False,
                              "guard.enabled": False, "collectors.scopeguard.report": False}
    assert cfg.events_wanted() == {"session_start": False, "stop": False, "session_end": False, "pre_tool_use": False}
    project.config(TEMPLATE)                                     # the shipped template is the light profile, valid
    assert load_config(project.root).switches() == cfg.switches()
    assert "profile: light" in TEMPLATE


def test_full_profile_and_explicit_keys_win(project):
    project.full()
    cfg = load_config(project.root)
    assert all(cfg.switches().values())
    assert all(cfg.events_wanted().values())
    project.config("profile: light\nguard:\n  enabled: true\nsessions:\n  record: true\nevents:\n")
    cfg = load_config(project.root)
    assert cfg.get("guard", "enabled") is True and cfg.pinned("guard", "enabled")
    assert cfg.get("sessions", "record") is True and not cfg.pinned("events", "stop")
    assert cfg.get("events", "stop") is False                    # an empty `events:` section keeps the profile's value
    assert cfg.events_wanted() == {"session_start": True, "stop": False, "session_end": True, "pre_tool_use": True}
    project.config("profile: loud\n")
    with pytest.raises(AuditGuardError):
        load_config(project.root)
    assert len(PROFILE_KEYS) == 5


def test_the_old_explicit_config_still_means_the_recorder(project):
    """A 0.1 config set every switch explicitly; without `profile` those pins keep the full behaviour."""
    project.config("render:\n  on_hook: true\nsessions:\n  record: true\nguard:\n  enabled: true\n"
                   "collectors:\n  scopeguard:\n    report: true\n")
    cfg = load_config(project.root)
    assert cfg.profile == "light" and cfg.get("guard", "enabled") is True and cfg.get("render", "on_hook") is True
    assert cfg.get("events", "stop") is False                    # the one switch 0.1 did not have


# ------------------------------------------------------------- execution


def test_light_runs_nothing_in_the_agent_loop(opened):
    root = str(opened.root)
    f = opened.feature()
    opened.event("session_start", {"hook_event_name": "SessionStart", "session_id": "abc", "source": "startup"})
    opened.hook("before_plan")
    started = [e for e in opened.events() if e["kind"] == "command.started"][0]
    assert "session" not in started["actor"] and opened.kinds("_project") == ["sprint.opened"]   # no session.started
    opened.event("stop", {"hook_event_name": "Stop"})
    assert "command.abandoned" not in opened.kinds()             # stop is a no-op
    edit = opened.ag("event", "pre_tool_use", code=None, stdin=json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": root,
         "tool_input": {"file_path": f"{root}/audit/sprints/S-1/_project/journal.jsonl"}}))
    assert edit.returncode == 0 and edit.stderr == ""            # the guard is off (archiGuard's edit guard covers audit/)
    assert not (opened.root / "audit/sprints/S-1/001-place-order/trail.md").exists()   # no view rebuilt per hook
    opened.hook("before_tasks")                                  # the next command closes the abandoned plan
    ab = [e for e in opened.events() if e["kind"] == "command.abandoned"]
    assert len(ab) == 1 and ab[0]["command"] == "speckit.plan" and ab[0]["source"] == "hook:next-command"
    opened.event("session_end", {"hook_event_name": "SessionEnd", "session_id": "abc"})
    assert opened.kinds("_project") == ["sprint.opened"]         # no session.ended (the event is unwired in light anyway)
    assert opened.kinds().count("command.abandoned") == 2        # ... but an open command is still closed with the session
    opened.ag("collect")                                         # explicit: the views follow
    assert (opened.root / "audit/sprints/S-1/001-place-order/trail.md").is_file()
    assert (opened.root / "audit/index.md").is_file()
    assert f.split("/")[-1] in (opened.root / "audit/index.md").read_text(encoding="utf-8")


# ------------------------------------------------------------- configure


def test_prune_native_events_keeps_everything_but_the_switched_off_entries():
    text = json.dumps(settings(), indent=2)
    light = {"session_start": False, "stop": False, "session_end": False, "pre_tool_use": False}
    new, present, removed = prune_native_events(text, light)
    assert removed == ["pre_tool_use", "session_end", "session_start", "stop"]
    assert present == light
    data = json.loads(new)
    assert set(data["hooks"]) == {"PreToolUse"}                  # the empty events are gone
    inner = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert inner == [dispatcher("speckit.archiguard.guard"), "./my-own-check.sh"]   # the sibling and the user's own hook stay
    assert data["permissions"] == {"allow": ["Bash(git:*)"]}
    # full: nothing removed, the text is returned as it was
    full = {k: True for k in light}
    same, present, removed = prune_native_events(text, full)
    assert same == text and removed == [] and all(present.values())
    # only sessions off: stop and the guard stay
    mixed = dict(full, session_start=False, session_end=False)
    new, present, removed = prune_native_events(text, mixed)
    assert removed == ["session_end", "session_start"] and set(json.loads(new)["hooks"]) == {"Stop", "PreToolUse"}
    # a file without hooks, and an unparsable one
    assert prune_native_events(json.dumps({"permissions": {}}), light)[2] == []
    with pytest.raises(AuditGuardError):
        prune_native_events("{not json", light)


def test_configure_unwires_the_events_of_the_light_profile(project):
    project.write(".claude/settings.json", json.dumps(settings(), indent=2) + "\n")
    out = project.ag("configure", "--dry-run").stdout
    assert "profile       : light" in out and "would change: .claude/settings.json: 4 agent event(s) unwired" in out
    assert json.loads((project.root / ".claude/settings.json").read_text(encoding="utf-8")) == settings()   # dry run
    out = project.ag("configure").stdout
    assert "changed: .claude/settings.json: 4 agent event(s) unwired (pre_tool_use, session_end, session_start, stop)" in out
    assert "agent events  : session_start off · stop off · session_end off · pre_tool_use off" in out
    assert "wired in      : .claude/settings.json -> none" in out
    data = json.loads((project.root / ".claude/settings.json").read_text(encoding="utf-8"))
    assert set(data["hooks"]) == {"PreToolUse"} and len(data["hooks"]["PreToolUse"]) == 2
    out = project.ag("configure").stdout                         # idempotent
    assert "changed:" not in out.replace("hook(s) switched", "")
    # the full profile wants them back: configure says how (Spec Kit re-registers them), it does not forge them
    project.full()
    out = project.ag("configure").stdout
    assert "profile       : full" in out
    assert "NOT WIRED     : session_start, stop, session_end, pre_tool_use" in out
    assert "specify extension disable auditguard && specify extension enable auditguard" in out
    assert json.loads((project.root / ".claude/settings.json").read_text(encoding="utf-8")) == data
    # pinned switches are marked, and only their events are unwired
    project.write(".claude/settings.json", json.dumps(settings(), indent=2) + "\n")
    project.config("profile: light\nguard:\n  enabled: true\n")
    out = project.ag("configure").stdout
    assert "pre_tool_use on (pinned)" in out and "3 agent event(s) unwired" in out
    data = json.loads((project.root / ".claude/settings.json").read_text(encoding="utf-8"))
    assert [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]] == [
        dispatcher("speckit.archiguard.guard"), dispatcher("speckit.auditguard.guard"), "./my-own-check.sh"]


def test_configure_json_carries_the_profile(project):
    data = json.loads(project.ag("configure", "--json").stdout)
    assert data["profile"] == "light" and data["events"]["stop"] is False and data["switches"]["render.on_hook"] is False
    project.full()
    data = json.loads(project.ag("configure", "--json").stdout)
    assert data["profile"] == "full" and all(data["events"].values()) and all(data["switches"].values())
