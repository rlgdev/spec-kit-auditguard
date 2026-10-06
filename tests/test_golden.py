"""Out-of-band changes (US5, FR-5xx) and verification against the golden sources (US7, FR-6xx): SC-003 and anchors."""

from __future__ import annotations

import json


def report(p):
    return json.loads((p.root / "audit" / "verify-report.json").read_text(encoding="utf-8"))


def designed(p):
    f = p.feature()
    p.hook("before_plan")
    p.write(f"{f}/plan.md", "# Plan\n")
    p.hook("after_plan")
    return f


def test_change_after_signoff_is_flagged(opened):
    f = designed(opened)
    opened.ag("decide", "design", "approve", "--by", "Tech lead")
    opened.append(f"{f}/plan.md", "\nquiet edit\n")
    opened.commit("tweak", author="J. Doe <jd@example.com>")
    opened.hook("before_implement")
    ch = [e for e in opened.events() if e["kind"] == "artefact.changed"][0]
    assert ch["data"]["after_signoff"] is True
    assert ch["data"]["inferred_command"] == "speckit.plan"
    assert "J. Doe" in list(ch["data"]["authors"].values())[0]
    out = opened.ag("check").stdout
    assert "[WARN] no_changes_after_signoff" in out
    assert opened.ag("check", AUDITGUARD_MODE="enforce", code=1)


def test_code_change_outside_a_command(opened):
    designed(opened)
    opened.append("src/app.py", "print('hotfix')\n")
    opened.ag("collect")
    ch = [e for e in opened.events() if e["kind"] == "artefact.changed"][-1]
    assert [c["path"] for c in ch["changed"]] == ["src/app.py"]
    assert ch["data"]["inferred_command"] == "speckit.implement"


def test_sc003_unexplained_commit_then_explained(opened):
    f = designed(opened)
    opened.commit("plan")
    opened.now = "2026-10-07T16:00:00+02:00"           # the next day, outside any command window
    opened.append(f"{f}/plan.md", "\nsneaky\n")
    sha = opened.commit("sneaky edit", author="J. Doe <jd@example.com>")
    proc = opened.ag("verify", "--golden", "--offline", code=1)
    assert "unexplained_commit" in proc.stdout
    f3 = [x for x in report(opened)["golden"]["findings"] if x["check"] == "G3"]
    assert f3[0]["commit"] == sha and f3[0]["author"] == "J. Doe" and f3[0]["files"] == [f"{f}/plan.md"]
    assert f3[0]["reproduce"].startswith("git show --stat")
    opened.ag("collect")                       # the trail now records the change (as out-of-band)
    opened.ag("verify", "--golden", "--offline")


def test_sc003_dirty_state_committed_later_is_verified(opened):
    f = designed(opened)                       # recorded while plan.md was uncommitted
    opened.commit("plan")
    opened.ag("verify", "--golden", "--offline")
    r = report(opened)["golden"]
    fin = [e for e in opened.events() if e["kind"] == "command.finished"][0]
    lab = r["events"][fin["hash"]]
    assert lab["checks"]["G2"] == "verified"
    assert lab["checks"]["G1"] == "unanchored"           # no remote in this repository
    head = opened.git("rev-parse", "HEAD").strip()
    assert lab["files"][f"{f}/plan.md"]["anchored_at"] == head
    assert lab["anchored_at"] == head


def test_sc003_state_that_never_reached_git_is_ephemeral(opened):
    f = designed(opened)
    opened.write(f"{f}/plan.md", "# Plan v2\n")          # changed again before any commit
    opened.hook("before_tasks")
    opened.commit("plan v2")
    opened.ag("verify", "--golden", "--offline")
    r = report(opened)["golden"]
    fin = [e for e in opened.events() if e["kind"] == "command.finished"][0]
    assert r["events"][fin["hash"]]["label"] == "ephemeral"
    assert r["status"] == "pass"


def test_rewritten_history_is_a_mismatch(opened):
    designed(opened)
    opened.commit("plan")
    opened.hook("before_tasks")                 # recorded on the commit that is about to disappear
    opened.git("commit", "--amend", "-q", "-m", "rewritten")
    opened.git("reflog", "expire", "--expire=now", "--all")
    opened.git("gc", "-q", "--prune=now")
    opened.ag("verify", "--golden", "--offline", code=1)
    assert any(x["check"] == "G1" and x["reason"] == "commit_unreachable" for x in report(opened)["golden"]["findings"])


def test_seal_and_anchor(opened):
    designed(opened)
    opened.commit("plan")
    opened.ag("sprint", "close", "--by", "Roman")
    opened.ag("anchor", "--sprint", "S-1", "--by", "Roman", code=2)    # HEAD does not contain the seal yet
    opened.commit("close S-1")
    opened.ag("anchor", "--sprint", "S-1", "--by", "Roman")
    opened.ag("verify", "--golden", "--offline")
    checks = report(opened)["golden"]["checks"]
    assert checks["G6"] == {"verified": 1}
    assert checks["G7"].get("verified", 0) > 0 and not checks["G7"].get("mismatch")
    # a truncated chain (tail removed) is invisible to the internal check, but not to the anchor
    path = opened.root / "audit/sprints/S-1/001-place-order/journal.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines(True)
    path.write_text("".join(lines[:-1]), encoding="utf-8")
    proc = opened.ag("verify", "--golden", "--offline", code=1)
    assert "rewritten_after_anchor" in proc.stdout or "seal" in proc.stdout


def test_signed_spec_must_match_the_handover_pin(opened):
    f = designed(opened)
    opened.write(f"{f}/handover.yml", "handover_version: 1\nsource:\n  producer: ba\n  version: '1'\n"
                 "  sha256: 'deadbeef'\n")
    opened.ag("decide", "design", "approve", "--by", "Tech lead")
    opened.commit("design")
    opened.ag("verify", "--golden", "--offline", code=1)
    assert any(x.get("reason") == "signed_spec_differs_from_handover" for x in report(opened)["golden"]["findings"])


def test_without_git_golden_is_skipped(tmp_path):
    from conftest import Project
    p = Project(tmp_path)
    p.write(".specify/init-options.json", "{}")
    p.ag("sprint", "open", "S-1", "--start", "2026-10-01", "--end", "2026-10-31", "--by", "Roman")
    p.ag("note", "no git here", "--by", "Roman")
    out = p.ag("verify", "--golden").stdout
    assert "SKIPPED" in out
