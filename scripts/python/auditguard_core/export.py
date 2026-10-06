"""Audit packs: a sealed sprint as a reproducible zip that verifies offline (`verify --pack`)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import AuditGuardError, read_bytes, read_json, rel_path, write_text
from .verify import verify_tree

FIXED_DATE = (2026, 1, 1, 0, 0, 0)

README = """auditGuard audit pack - sprint {sprint}

This archive holds the audit trail of one sprint of a Spec Kit project, as recorded by auditGuard
(https://github.com/rlgdev/spec-kit-auditguard):

  sprints/{sprint}/<feature>/journal.jsonl   the record: one hash-chained JSON event per line
  sprints/{sprint}/<feature>/evidence/       content-addressed snapshots the events reference
  sprints/{sprint}/seal.json                 the seal written when the sprint was closed
  sprints/{sprint}/**/trail.md, sprint.md    generated views (not evidence)
  sprints.yml                                the sprint register
  chain-links.json                           where each chain continues from an earlier sprint
  verify-report.json                         the verification at export time (internal + golden)
  viewer/index.html                          open in a browser - works offline from the file system
  SHA256SUMS                                 sha256 of every file above

Verify it, without the repository:

  python <auditguard>/scripts/python/auditguard.py verify --pack audit-pack-{sprint}.zip

or check the files by hand: every line of a journal has `hash` = SHA-256 of the canonical JSON of the line
without `hash` (sorted keys, separators "," and ":", UTF-8), and `prev` = the `hash` of the event with the
previous `seq` of the same chain.
"""


def build_pack(root: Path, cfg: Any, sprint: str, out: Optional[Path], verification: Dict[str, Any],
               viewer_files: Dict[str, bytes]) -> Tuple[Path, int]:
    audit = cfg.audit_root
    folder = audit / "sprints" / sprint
    if not folder.is_dir():
        raise AuditGuardError(f"no folder for sprint {sprint} under {rel_path(audit, root)}/sprints")
    target = out or (root / f"audit-pack-{sprint}.zip")
    if target.is_dir():
        target = target / f"audit-pack-{sprint}.zip"
    entries: Dict[str, bytes] = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            entries[f"sprints/{sprint}/{path.relative_to(folder).as_posix()}"] = read_bytes(path)
    if cfg.register_path.is_file():
        entries["sprints.yml"] = read_bytes(cfg.register_path)
    # chain links: the event each chain of this sprint continues from
    from .store import Store
    store = Store(root, cfg)
    links = {}
    for jdir in sorted(p for p in folder.iterdir() if p.is_dir()):
        evs, _ = store.read_journal(jdir / "journal.jsonl")
        if evs:
            links[jdir.name] = {"first_seq": evs[0].get("seq"), "prev": evs[0].get("prev")}
    entries["chain-links.json"] = (json.dumps(links, indent=2, sort_keys=True) + "\n").encode("utf-8")
    entries["verify-report.json"] = (json.dumps(verification, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    for name, data in viewer_files.items():
        entries[f"viewer/{name}"] = data
    entries["README.txt"] = README.format(sprint=sprint).encode("utf-8")
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(entries.items()))
    entries["SHA256SUMS"] = sums.encode("utf-8")
    prefix = f"audit-pack-{sprint}/"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as zf:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(prefix + name, date_time=FIXED_DATE)
            info.external_attr = (0o100644 & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return target, len(entries)


def verify_pack(pack: Path) -> Dict[str, Any]:
    if not pack.is_file():
        raise AuditGuardError(f"pack not found: {pack}")
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(pack) as zf:
            for name in zf.namelist():
                if name.startswith("/") or ".." in Path(name).parts:
                    raise AuditGuardError(f"unsafe path in the pack: {name}")
            zf.extractall(tmp)
        roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise AuditGuardError("the pack must contain exactly one top-level folder")
        base = roots[0]
        problems: List[Dict[str, Any]] = []
        sums = base / "SHA256SUMS"
        if not sums.is_file():
            problems.append({"kind": "sums", "where": "SHA256SUMS", "message": "missing"})
        else:
            listed = {}
            for line in sums.read_text(encoding="utf-8").splitlines():
                if "  " in line:
                    digest, name = line.split("  ", 1)
                    listed[name] = digest
            for path in sorted(base.rglob("*")):
                if not path.is_file() or path.name == "SHA256SUMS":
                    continue
                name = path.relative_to(base).as_posix()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if name not in listed:
                    problems.append({"kind": "sums", "where": name, "message": "file not listed in SHA256SUMS"})
                elif listed[name] != digest:
                    problems.append({"kind": "sums", "where": name, "message": "sha256 differs from SHA256SUMS"})
            for name in listed:
                if not (base / name).is_file():
                    problems.append({"kind": "sums", "where": name, "message": "listed in SHA256SUMS but missing"})
        links = read_json(base / "chain-links.json", {}) or {}
        result = verify_tree(base, base / "sprints.yml", partial_links=links)
        result["problems"] = problems + result["problems"]
        result["status"] = "pass" if not result["problems"] else "fail"
        result["pack"] = str(pack)
        return result


def write_text_file(path: Path, text: str) -> None:
    write_text(path, text)
