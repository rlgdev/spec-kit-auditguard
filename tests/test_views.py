"""Views and packs: SC-004 (audit pack verifies offline), SC-005 (deterministic rendering), the viewer (SC-007),
the guard (SC-008), the sprint rules (SC-010), configuration and the human-only commands."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest

from conftest import REPO


def small_story(p):
    f = p.feature()
    p.hook("before_plan")
    p.write(f"{f}/plan.md", "# Plan\n\n## Scope Coverage\n\n| ID | Title | Status | Plan reference | Reason |\n|---|---|---|---|---|\n"
            "| US1 | x | covered | a | |\n| FR-009 | y | deferred | | phase 2 |\n")
    p.hook("after_plan")
    p.ag("decide", "design", "approve", "--by", "Tech lead", "--role", "tech lead")
    p.commit("design")
    return f


def test_sc004_pack_verifies_offline_and_detects_tampering(opened, tmp_path):
    small_story(opened)
    opened.ag("sprint", "close", "--by", "Roman")
    opened.commit("close")
    pack = tmp_path / "pack.zip"
    opened.ag("export", "--sprint", "S-1", "--out", str(pack))
    assert opened.ag("verify", "--pack", str(pack)).returncode == 0
    names = zipfile.ZipFile(pack).namelist()
    assert "audit-pack-S-1/viewer/index.html" in names and "audit-pack-S-1/SHA256SUMS" in names
    # same input -> same archive
    pack2 = tmp_path / "pack2.zip"
    opened.ag("export", "--sprint", "S-1", "--out", str(pack2))
    assert pack.read_bytes() == pack2.read_bytes()
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(pack) as zin, zipfile.ZipFile(bad, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.endswith("001-place-order/journal.jsonl"):
                data = data.replace(b"Tech lead", b"Tech Lead")
            zout.writestr(info, data)
    out = opened.ag("verify", "--pack", str(bad), code=1).stdout
    assert "FAIL" in out


def test_sc005_render_is_deterministic(opened):
    small_story(opened)
    opened.ag("render", "--html")
    snap = {p: p.read_bytes() for p in (opened.root / "audit").rglob("*") if p.is_file() and p.suffix in (".md", ".js")}
    out = opened.ag("render", "--html").stdout
    assert "0 file(s) updated" in out
    assert snap == {p: p.read_bytes() for p in (opened.root / "audit").rglob("*") if p.is_file() and p.suffix in (".md", ".js")}


def test_trail_markdown_has_the_sections(opened):
    small_story(opened)
    text = (opened.root / "audit/sprints/S-1/001-place-order/trail.md").read_text(encoding="utf-8")
    for heading in ("## Design", "## Decisions (human in the loop)", "## Waivers and deferrals", "## Gate verdicts",
                    "## Changes outside recorded commands"):
        assert heading in text
    assert "defer:FR-009" in text and "Tech lead" in text
    index = (opened.root / "audit/index.md").read_text(encoding="utf-8")
    assert "001-place-order" in index and "S-1" in index


def test_viewer_is_offline_and_small():
    html = (REPO / "templates/viewer/index.html").read_text(encoding="utf-8")
    assert len(html.encode("utf-8")) < 300_000
    assert not re.search(r"""(src|href)\s*=\s*["']https?://""", html)
    assert "@import" not in html and "fonts.googleapis" not in html
    assert '<script src="data.js"></script>' in html


def test_viewer_data_bundle(opened):
    small_story(opened)
    opened.ag("verify", "--golden", "--offline")
    opened.ag("render", "--html")
    text = (opened.root / "audit/viewer/data.js").read_text(encoding="utf-8")
    data = json.loads(text.split("window.AUDITGUARD = ", 1)[1].rstrip().rstrip(";"))
    assert data["events"] and all("_title" in e and "_label" in e for e in data["events"])
    assert [s["id"] for s in data["register"]] == ["S-1"]
    assert "</script" not in text


def _viewer_smoke(index: Path):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"no chromium: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("http*://**", lambda route: route.abort())       # no network
        page.goto(index.as_uri() + "#/")
        assert page.locator("table.matrix").count() == 1
        page.goto(index.as_uri() + "#/feature/001-place-order")
        assert page.locator("ol.chain li").count() > 3
        page.locator("ol.chain li .ev-title").first.click()
        assert page.locator(".record").count() == 1
        page.goto(index.as_uri() + "#/verification")
        assert page.locator("h1").inner_text() == "Verification"
        browser.close()
        assert errors == []


def test_sc007_viewer_renders_from_file(opened):
    small_story(opened)
    opened.ag("verify", "--golden", "--offline")
    opened.ag("render", "--html")
    _viewer_smoke(opened.root / "audit/viewer/index.html")


# ------------------------------------------------------------------- guard


def guard(p, payload):
    return p.ag("event", "pre_tool_use", stdin=json.dumps(payload), code=None)


def test_sc008_guard_blocks_agent_edits_and_human_commands(opened):
    root = str(opened.root)
    edit = guard(opened, {"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": root,
                          "tool_input": {"file_path": f"{root}/audit/sprints/S-1/_project/journal.jsonl"}})
    assert edit.returncode == 2 and "read-only" in edit.stderr
    for cmd in ("bash .specify/extensions/auditguard/scripts/bash/auditguard.sh decide design approve --by me",
                "python .specify/extensions/auditguard/scripts/python/auditguard.py sprint close --by x",
                "auditguard anchor --push", "echo forged >> audit/sprints/S-1/_project/journal.jsonl",
                "rm -rf audit/sprints", "sed -i 's/a/b/' audit/sprints.yml"):
        proc = guard(opened, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}})
        assert proc.returncode == 2, cmd
    for cmd in ("bash .specify/extensions/auditguard/scripts/bash/auditguard.sh hook after_plan --via hooks",
                "git add audit && git commit -m 'audit'", "cat audit/index.md", "auditguard verify --golden"):
        proc = guard(opened, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}})
        assert proc.returncode == 0, cmd
    ok = guard(opened, {"tool_name": "Edit", "cwd": root, "tool_input": {"file_path": f"{root}/src/app.py"}})
    assert ok.returncode == 0
    assert guard(opened, {"garbage": True}).returncode == 0


def test_human_commands_refuse_an_agent_context(opened):
    opened.feature()
    proc = opened.ag("decide", "design", "approve", "--by", "x", AUDITGUARD_CONTEXT="agent", code=3)
    assert "agent context" in proc.stderr
    opened.ag("note", "x", "--by", "y", AUDITGUARD_CONTEXT="agent", code=3)


def test_decide_needs_a_person(opened):
    opened.feature()
    proc = opened.ag("decide", "pr", "approve", code=2)
    assert "--by" in proc.stderr


# ------------------------------------------------------------------- rules


def test_sc010_close_with_an_open_escalation(opened):
    (opened.root / ".specify/extensions/archiguard").mkdir(parents=True)
    f = opened.feature()
    opened.hook("before_tasks")
    opened.write(f"{f}/gates/escalation-tasks-b.md", "TODO(agent) ARCH-201\n")
    opened.event("stop", {"hook_event_name": "Stop"})
    proc = opened.ag("sprint", "close", "--by", "Roman", AUDITGUARD_MODE="enforce", code=1)
    assert "escalations_decided_before_close" in proc.stderr
    out = opened.ag("sprint", "close", "--by", "Roman").stdout
    assert "[WARN] escalations_decided_before_close" in out
    seal = json.loads((opened.root / "audit/sprints/S-1/seal.json").read_text(encoding="utf-8"))
    assert any(w["rule"] == "escalations_decided_before_close" for w in seal["warnings"])


def test_escalation_decision_closes_it(opened):
    (opened.root / ".specify/extensions/archiguard").mkdir(parents=True)
    f = opened.feature()
    opened.hook("before_tasks")
    opened.write(f"{f}/gates/escalation-tasks-b.md", "TODO(agent) ARCH-201\n")
    opened.event("stop", {"hook_event_name": "Stop"})
    opened.ag("decide", "escalation:escalation-tasks-b.md", "accept", "--by", "Roman", "--role", "lead architect",
              "--reason", "add the task")
    assert "escalations_decided_before_close" not in opened.ag("check").stdout.split("[WARN]", 1)[-1] or \
        "[OK]   escalations_decided_before_close" in opened.ag("check").stdout


def test_implement_before_signed_design(opened):
    opened.feature()
    opened.hook("before_implement")
    assert "[WARN] implement_requires_design_signed" in opened.ag("check").stdout


# ------------------------------------------------------------------- config


def test_unknown_config_key_is_an_error(opened):
    opened.write(".specify/extensions/auditguard/auditguard-config.yml", "mode: record\nmodes: enforce\n")
    proc = opened.ag("check", code=2)
    assert "unknown setting modes" in proc.stderr


def test_local_config_may_not_change_the_mode(opened):
    opened.write(".specify/extensions/auditguard/local-config.yml", "mode: enforce\nintegration: workflow\n")
    out = opened.ag("configure").stdout
    assert "integration   : workflow" in out and "mode          : record" in out
    out = opened.ag("configure", CI="true").stdout
    assert "integration   : hooks" in out


def test_configure_switches_the_hooks(opened):
    opened.write(".specify/extensions.yml", "installed: [auditguard]\nhooks:\n  before_plan:\n  - extension: auditguard\n"
                 "    command: speckit.auditguard.planentry\n    enabled: true\n    optional: false\n")
    opened.write(".specify/extensions/auditguard/auditguard-config.yml", "integration: workflow\n")
    opened.ag("configure")
    assert "enabled: false" in (opened.root / ".specify/extensions.yml").read_text(encoding="utf-8")
    ga = (opened.root / ".gitattributes").read_text(encoding="utf-8")
    assert "audit/**/*.jsonl -text" in ga


def test_configure_keeps_crlf_line_endings(opened):
    """Spec Kit writes .specify/extensions.yml with CRLF on Windows: configure keeps them, so git shows one line."""
    registry = ("installed: [git, auditguard]\nhooks:\n  after_plan:\n  - extension: git\n    command: speckit.git.commit\n"
                "    enabled: true\n  - extension: auditguard\n    command: speckit.auditguard.planexit\n    enabled: true\n")
    before = registry.replace("\n", "\r\n").encode("utf-8")
    (opened.root / ".specify/extensions.yml").write_bytes(before)
    (opened.root / ".gitattributes").write_bytes(b"*.sh text eol=lf\r\n")
    opened.write(".specify/extensions/auditguard/auditguard-config.yml", "integration: workflow\n")
    opened.ag("configure")
    after = (opened.root / ".specify/extensions.yml").read_bytes()
    assert b"\n" not in after.replace(b"\r\n", b""), after          # every line still ends with CRLF
    changed = [(a, b) for a, b in zip(before.split(b"\r\n"), after.split(b"\r\n")) if a != b]
    assert changed == [(b"    enabled: true", b"    enabled: false")]
    ga = (opened.root / ".gitattributes").read_bytes()
    assert ga.startswith(b"*.sh text eol=lf\r\n") and b"audit/**/*.jsonl -text\r\n" in ga
    assert b"\n" not in ga.replace(b"\r\n", b""), ga
