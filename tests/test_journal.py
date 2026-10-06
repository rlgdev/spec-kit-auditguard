"""The chain: append, cross-sprint links, sealing, redirects, locking - and SC-002 (every mutation is detected)."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

from auditguard_core.common import GENESIS, AuditGuardError
from auditguard_core.config import load_config
from auditguard_core.store import Store, event_hash
from auditguard_core.verify import verify_tree


def store_of(project):
    return Store(project.root, load_config(project.root))


def test_append_builds_a_chain(opened, monkeypatch):
    monkeypatch.setenv("AUDITGUARD_NOW", "2026-10-06T10:00:00+02:00")
    s = store_of(opened)
    a = s.append("001-x", {"kind": "note", "feature": "specs/001-x", "data": {"text": "one"}})
    b = s.append("001-x", {"kind": "note", "feature": "specs/001-x", "data": {"text": "two"}})
    assert (a["seq"], a["prev"]) == (1, GENESIS)
    assert (b["seq"], b["prev"]) == (2, a["hash"])
    assert b["hash"] == event_hash(b)
    assert a["sprint"] == "S-1"
    assert verify_tree(opened.root / "audit")["status"] == "pass"


def test_chain_continues_across_sprints(opened):
    opened.ag("note", "first", "--by", "Roman")
    opened.ag("sprint", "close", "S-1", "--by", "Roman")
    opened.now = "2026-11-03T09:00:00+01:00"
    opened.ag("sprint", "open", "S-2", "--start", "2026-11-01", "--end", "2026-11-14", "--by", "Roman")
    opened.ag("note", "second", "--by", "Roman")
    evs = opened.events("_project")
    assert [e["sprint"] for e in evs] == ["S-1", "S-1", "S-1", "S-2", "S-2"]
    for prev, cur in zip(evs, evs[1:]):
        assert cur["prev"] == prev["hash"] and cur["seq"] == prev["seq"] + 1
    assert opened.ag("verify").returncode == 0


def test_append_after_seal_is_redirected(opened):
    opened.ag("note", "before", "--by", "Roman")
    opened.ag("sprint", "close", "S-1", "--by", "Roman")
    s = store_of(opened)
    ev = s.append("_project", {"kind": "note", "data": {"text": "late"}}, sprint="S-1")
    assert ev["sprint"] == "_unassigned"
    assert ev["data"]["redirected_from"] == "S-1"
    assert verify_tree(opened.root / "audit")["status"] == "pass"


def test_without_a_sprint_events_go_to_unassigned(project):
    project.ag("note", "nobody opened a sprint", "--by", "Roman")
    assert project.events("_project")[0]["sprint"] == "_unassigned"
    out = project.ag("check").stdout
    assert "[WARN] no_unassigned_events" in out


def _journal(project, key="_project", sprint="S-1") -> Path:
    return project.root / "audit" / "sprints" / sprint / key / "journal.jsonl"


def _three_notes(project):
    for text in ("a", "b", "c"):
        project.ag("note", text, "--by", "Roman")


def _problems(project):
    return verify_tree(project.root / "audit")["problems"]


def test_sc002_edited_byte_is_detected(opened):
    _three_notes(opened)
    path = _journal(opened)
    path.write_text(path.read_text(encoding="utf-8").replace('"text": "b"', '"text": "B"'), encoding="utf-8")
    assert any(p["kind"] == "hash" for p in _problems(opened))
    assert opened.ag("verify", code=1)


def test_sc002_deleted_line_is_detected(opened):
    _three_notes(opened)
    path = _journal(opened)
    lines = path.read_text(encoding="utf-8").splitlines(True)
    path.write_text("".join(lines[:2] + lines[3:]), encoding="utf-8")
    kinds = {p["kind"] for p in _problems(opened)}
    assert {"gap", "link"} & kinds


def test_sc002_deleted_sprint_folder_is_detected(opened):
    opened.ag("note", "in S-1", "--by", "Roman")
    opened.ag("sprint", "close", "S-1", "--by", "Roman")
    opened.now = "2026-11-03T09:00:00+01:00"
    opened.ag("sprint", "open", "S-2", "--start", "2026-11-01", "--end", "2026-11-14", "--by", "Roman")
    opened.ag("note", "in S-2", "--by", "Roman")
    shutil.rmtree(opened.root / "audit" / "sprints" / "S-1")
    kinds = {p["kind"] for p in _problems(opened)}
    assert "gap" in kinds and "register" not in kinds or "gap" in kinds


def test_sc002_reordered_lines_are_detected(opened):
    _three_notes(opened)
    path = _journal(opened)
    lines = path.read_text(encoding="utf-8").splitlines(True)
    lines[1], lines[2] = lines[2], lines[1]
    path.write_text("".join(lines), encoding="utf-8")
    kinds = {p["kind"] for p in _problems(opened)}
    assert "order" in kinds


def test_sc002_changed_evidence_is_detected(opened):
    s = store_of(opened)
    ev = s.append("_project", {"kind": "note", "data": {}}, evidence=[("report.json", b'{"a": 1}\n', None)])
    target = s.sprints_dir / "S-1" / "_project" / ev["evidence"][0]["path"]
    target.write_text('{"a": 2}\n', encoding="utf-8")
    assert any(p["kind"] == "evidence" for p in _problems(opened))


def test_sc002_line_appended_after_seal_is_detected(opened):
    opened.ag("note", "x", "--by", "Roman")
    opened.ag("sprint", "close", "S-1", "--by", "Roman")
    path = _journal(opened)
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    forged = dict(events[-1], seq=events[-1]["seq"] + 1, prev=events[-1]["hash"], kind="note", data={"text": "forged"})
    forged["hash"] = event_hash(forged)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(forged) + "\n")
    assert any(p["kind"] == "seal" for p in _problems(opened))


def test_refuses_to_extend_a_broken_chain(opened):
    _three_notes(opened)
    path = _journal(opened)
    path.write_text(path.read_text(encoding="utf-8").replace('"text": "c"', '"text": "C"'), encoding="utf-8")
    with pytest.raises(AuditGuardError):
        store_of(opened).append("_project", {"kind": "note", "data": {}})


def test_concurrent_appends_stay_contiguous(opened):
    s1, s2 = store_of(opened), store_of(opened)

    def writer(store, tag):
        for i in range(15):
            store.append("001-x", {"kind": "note", "feature": "specs/001-x", "data": {"text": f"{tag}{i}"}})

    threads = [threading.Thread(target=writer, args=(s, t)) for s, t in ((s1, "a"), (s2, "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    events = opened.events("001-x")
    assert [e["seq"] for e in events] == list(range(1, 31))
    assert verify_tree(opened.root / "audit")["status"] == "pass"


def test_journal_lines_are_lf_utf8(opened):
    opened.ag("note", "zażółć gęślą jaźń", "--by", "Roman")
    data = _journal(opened).read_bytes()
    assert b"\r\n" not in data
    assert "zażółć".encode("utf-8") in data


def test_register_rejects_overlapping_sprints(opened):
    out = opened.ag("sprint", "open", "S-2", "--start", "2026-10-15", "--end", "2026-11-15", "--by", "Roman",
                    "--close-current", code=2)
    assert "overlap" in out.stderr or "before sprint" in out.stderr
