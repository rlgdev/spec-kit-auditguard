"""Internal verification (FR-205): chains, cross-sprint links, evidence hashes, seals and the sprint register.

Works on a project (`audit/` in a repository) and on an extracted audit pack (no repository needed)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .common import GENESIS, PROJECT_KEY, UNASSIGNED, content_hash, read_bytes, read_json, read_text, sha256_json
from .sprints import Register
from .store import event_hash


class Problem(dict):
    pass


def _problem(kind: str, where: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"kind": kind, "where": where, "message": message, **extra}


def verify_tree(audit_root: Path, register_path: Optional[Path] = None, *,
                partial_links: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Verify every journal under audit_root/sprints.

    `partial_links` (audit packs): {chain: {first_seq, prev}} - a chain that continues from a sprint outside the
    pack starts at first_seq with that prev instead of seq 1 and the genesis hash."""
    sprints_dir = audit_root / "sprints"
    register = Register.load(register_path or audit_root / "sprints.yml")
    problems: List[Dict[str, Any]] = []
    for msg in register.problems():
        problems.append(_problem("register", str(register.path.name), msg))
    folders = sorted([p.name for p in sprints_dir.iterdir() if p.is_dir()], key=register.order_key) if sprints_dir.is_dir() else []
    chains: Dict[str, List[Dict[str, Any]]] = {}
    files_per_folder: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    n_events = 0
    n_evidence = 0
    for folder in folders:
        files_per_folder[folder] = {}
        for jdir in sorted(p for p in (sprints_dir / folder).iterdir() if p.is_dir()):
            jpath = jdir / "journal.jsonl"
            if not jpath.is_file():
                continue
            rel = f"sprints/{folder}/{jdir.name}/journal.jsonl"
            events: List[Dict[str, Any]] = []
            for no, raw in enumerate(read_text(jpath).split("\n"), start=1):
                if not raw.strip():
                    continue
                try:
                    ev = json.loads(raw)
                except ValueError as exc:
                    problems.append(_problem("json", f"{rel}:{no}", f"not valid JSON ({exc})"))
                    continue
                if not isinstance(ev, dict):
                    problems.append(_problem("json", f"{rel}:{no}", "not a JSON object"))
                    continue
                where = f"{rel}:{no}"
                if ev.get("hash") != event_hash(ev):
                    problems.append(_problem("hash", where, "the hash does not match the content - the event was edited",
                                             seq=ev.get("seq"), chain=jdir.name))
                if ev.get("sprint") != folder:
                    problems.append(_problem("folder", where, f"the event says sprint {ev.get('sprint')!r} but is stored in {folder}"))
                key = Path(str(ev.get("feature"))).name if ev.get("feature") else PROJECT_KEY
                if key != jdir.name:
                    problems.append(_problem("folder", where, f"the event belongs to chain {key!r} but is stored under {jdir.name}"))
                ev["_where"] = where
                ev["_folder"] = folder
                events.append(ev)
                # evidence
                for ref in ev.get("evidence") or []:
                    if not ref.get("snapshot") or not ref.get("path"):
                        continue
                    n_evidence += 1
                    epath = jdir / str(ref["path"])
                    if not epath.is_file():
                        problems.append(_problem("evidence", where, f"evidence {ref['path']} is missing"))
                    elif content_hash(read_bytes(epath)) != ref.get("sha256"):
                        problems.append(_problem("evidence", where, f"evidence {ref['path']} was changed (sha256 differs)"))
            seqs = [e.get("seq") for e in events]
            if seqs != sorted(s for s in seqs if isinstance(s, int)) or len(set(seqs)) != len(seqs):
                problems.append(_problem("order", rel, "events are not stored in increasing seq order"))
            files_per_folder[folder][jdir.name] = events
            chains.setdefault(jdir.name, []).extend(events)
            n_events += len(events)
    # chains across sprints
    heads: Dict[str, Dict[str, Any]] = {}
    for key, events in sorted(chains.items()):
        events.sort(key=lambda e: e.get("seq") if isinstance(e.get("seq"), int) else 0)
        prev_hash = GENESIS
        expected = 1
        link = (partial_links or {}).get(key)
        if link and events and events[0].get("seq") == link.get("first_seq"):
            prev_hash = link.get("prev") or GENESIS
            expected = int(link.get("first_seq") or 1)
        for ev in events:
            seq = ev.get("seq")
            if seq != expected:
                problems.append(_problem("gap", ev["_where"], f"chain '{key}': expected seq {expected}, found {seq} - "
                                         f"events {expected}..{(seq or 0) - 1} are missing (deleted lines or a deleted "
                                         "sprint folder)" if isinstance(seq, int) and seq > expected else
                                         f"chain '{key}': seq {seq} out of place (expected {expected})", chain=key))
                expected = seq if isinstance(seq, int) else expected
            if ev.get("prev") != prev_hash:
                problems.append(_problem("link", ev["_where"], f"chain '{key}': prev does not match the hash of seq "
                                         f"{(seq or 1) - 1} - the chain is broken", chain=key))
            prev_hash = ev.get("hash") or ""
            expected = (seq if isinstance(seq, int) else expected) + 1
        if events:
            heads[key] = {"seq": events[-1].get("seq"), "hash": events[-1].get("hash"), "sprint": events[-1].get("_folder")}
    # seals
    seals: Dict[str, Any] = {}
    for folder in folders:
        seal_path = sprints_dir / folder / "seal.json"
        entry = register.get(folder)
        if not seal_path.is_file():
            if entry and entry.get("status") == "closed":
                problems.append(_problem("seal", f"sprints/{folder}", "the register says closed but seal.json is missing"))
            continue
        seal = read_json(seal_path, None)
        if not isinstance(seal, dict):
            problems.append(_problem("seal", f"sprints/{folder}/seal.json", "not valid JSON"))
            continue
        ok = True
        if seal.get("hash") != sha256_json({k: v for k, v in seal.items() if k != "hash"}):
            problems.append(_problem("seal", f"sprints/{folder}/seal.json", "the seal's hash does not match its content"))
            ok = False
        listed = seal.get("journals") or {}
        present = files_per_folder.get(folder, {})
        for key, events in present.items():
            name = f"{key}/journal.jsonl"
            info = listed.get(name)
            if info is None:
                problems.append(_problem("seal", f"sprints/{folder}/{name}", "journal created after the sprint was sealed"))
                ok = False
                continue
            if events and info.get("first_seq") is not None and events[0].get("seq") != info.get("first_seq"):
                problems.append(_problem("seal", f"sprints/{folder}/{name}",
                                         f"the sealed journal started at seq {info.get('first_seq')}, now at {events[0].get('seq')}"))
                ok = False
            if len(events) != info.get("count") or (events and events[-1].get("hash") != info.get("head")):
                problems.append(_problem("seal", f"sprints/{folder}/{name}",
                                         f"journal changed after the seal (sealed {info.get('count')} events, head "
                                         f"{str(info.get('head'))[:12]}; now {len(events)}, head "
                                         f"{str(events[-1].get('hash') if events else None)[:12]})"))
                ok = False
        for name in listed:
            if name.split("/")[0] not in present:
                problems.append(_problem("seal", f"sprints/{folder}/{name}", "a sealed journal is missing"))
                ok = False
        hashes = []
        for jdir in (p for p in (sprints_dir / folder).iterdir() if p.is_dir()):
            ev_dir = jdir / "evidence"
            if ev_dir.is_dir():
                hashes += [content_hash(read_bytes(f)) for f in ev_dir.iterdir() if f.is_file()]
        ev_info = seal.get("evidence") or {}
        if ev_info.get("count") != len(hashes) or ev_info.get("sha256_of_sorted_hashes") != sha256_json(sorted(hashes)):
            problems.append(_problem("seal", f"sprints/{folder}/evidence", "the evidence of the sealed sprint changed"))
            ok = False
        if entry is not None:
            reg_seal = (entry.get("closed") or {}).get("seal")
            if entry.get("status") != "closed":
                problems.append(_problem("seal", f"sprints/{folder}", "sealed, but the register does not say closed"))
                ok = False
            elif reg_seal and reg_seal != seal.get("hash"):
                problems.append(_problem("seal", f"sprints/{folder}", "the register records a different seal hash"))
                ok = False
        seals[folder] = {"hash": seal.get("hash"), "ok": ok, "at": seal.get("at"), "closed_by": seal.get("closed_by")}
    return {"status": "pass" if not problems else "fail", "journals": sum(len(v) for v in files_per_folder.values()),
            "events": n_events, "evidence": n_evidence, "chains": heads, "seals": seals, "problems": problems,
            "folders": folders, "unassigned": UNASSIGNED in folders}


def render_internal(result: Dict[str, Any]) -> List[str]:
    lines = []
    if result["status"] == "pass":
        lines.append(f"internal : PASS   {len(result['chains'])} chain(s) intact · {len(result['seals'])} seal(s) match · "
                     f"{result['evidence']} evidence hash(es) match · register consistent")
    else:
        lines.append(f"internal : FAIL   {len(result['problems'])} problem(s)")
        for p in result["problems"][:50]:
            lines.append(f"  [{p['kind']}] {p['where']}: {p['message']}")
        if len(result["problems"]) > 50:
            lines.append(f"  ... {len(result['problems']) - 50} more")
    return lines
