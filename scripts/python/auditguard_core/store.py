"""The audit store: hash-chained journals partitioned by sprint, one chain per feature, and the evidence store.

Layout (under audit.root, default audit/):

    sprints/<sprint>/<key>/journal.jsonl     one JSON object per line, hash-chained
    sprints/<sprint>/<key>/evidence/         content-addressed snapshots: <sha256[:12]>-<name>
    sprints/<sprint>/seal.json               written when the sprint is closed

<key> is the feature directory name (specs/<key>) or `_project`. A chain spans sprints: `seq` counts the
events of the chain across all sprint folders (1, 2, 3, ...) and `prev` is the hash of the event with the
previous `seq`, wherever it is stored, so a deleted sprint folder breaks the chain just like an edited line.
`hash` is the SHA-256 of the canonical JSON of the event without `hash` - the scheme of archiGuard's ledger.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .common import (GENESIS, PROJECT_KEY, STATE_REL, UNASSIGNED, AuditGuardError, canonical_json, content_hash,
                     normalise_content, now, now_iso, parse_time, read_json, read_text, sha256_text, time_key, today,
                     write_bytes)
from .config import Config
from .sprints import Register

SCHEMA_VERSION = 1
LOCK_TIMEOUT = 10.0
STALE_LOCK = 120.0


def event_hash(event: Dict[str, Any]) -> str:
    return sha256_text(canonical_json({k: v for k, v in event.items() if k != "hash"}))


def journal_line(event: Dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(", ", ": "))


def safe_name(name: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name.replace("\\", "/").split("/")[-1])
    return keep[:80] or "file"


class ChainProblem(Exception):
    pass


class Store:
    def __init__(self, root: Path, cfg: Config):
        self.root = root
        self.cfg = cfg
        self.audit = cfg.audit_root
        self.sprints_dir = self.audit / "sprints"
        self.register = Register.load(cfg.register_path)
        self._chains: Dict[str, List[Dict[str, Any]]] = {}

    # ----------------------------------------------------------------- paths
    def journal_path(self, sprint: str, key: str) -> Path:
        return self.sprints_dir / sprint / key / "journal.jsonl"

    def sprint_dirs(self) -> List[str]:
        if not self.sprints_dir.is_dir():
            return []
        names = [p.name for p in self.sprints_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
        return sorted(names, key=self.register.order_key)

    def sealed(self, sprint: str) -> bool:
        entry = self.register.get(sprint)
        return (self.sprints_dir / sprint / "seal.json").is_file() or bool(entry and entry.get("status") == "closed")

    def journal_files(self, key: str) -> List[Tuple[str, Path]]:
        out = []
        for sprint in self.sprint_dirs():
            p = self.journal_path(sprint, key)
            if p.is_file():
                out.append((sprint, p))
        return out

    def chain_keys(self) -> List[str]:
        keys = set()
        for sprint in self.sprint_dirs():
            for p in (self.sprints_dir / sprint).iterdir():
                if p.is_dir() and (p / "journal.jsonl").is_file():
                    keys.add(p.name)
        return sorted(keys, key=lambda k: (k != PROJECT_KEY, k))

    # ------------------------------------------------------------------ read
    @staticmethod
    def read_journal(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
        events, problems = [], []
        if not path.is_file():
            return events, problems
        for no, raw in enumerate(read_text(path).split("\n"), start=1):
            if not raw.strip():
                continue
            try:
                ev = json.loads(raw)
            except ValueError as exc:
                problems.append(f"{path}:{no}: not valid JSON ({exc})")
                continue
            if not isinstance(ev, dict):
                problems.append(f"{path}:{no}: not a JSON object")
                continue
            ev["_line"] = no
            events.append(ev)
        return events, problems

    def load_chain(self, key: str, *, refresh: bool = False) -> List[Dict[str, Any]]:
        """Every event of a chain across sprints, ordered by seq (tolerant: verify reports the problems)."""
        if key in self._chains and not refresh:
            return self._chains[key]
        events: List[Dict[str, Any]] = []
        for sprint, path in self.journal_files(key):
            evs, _ = self.read_journal(path)
            for ev in evs:
                ev["_sprint_dir"] = sprint
                ev["_journal"] = path
            events.extend(evs)
        events.sort(key=lambda e: (e.get("seq") if isinstance(e.get("seq"), int) else 0))
        self._chains[key] = events
        return events

    def all_events(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for key in self.chain_keys():
            out.extend(self.load_chain(key))
        return sorted(out, key=lambda e: (time_key(e.get("at")), e.get("feature") or "", e.get("seq") or 0))

    def head(self, key: str) -> Tuple[int, str]:
        best_seq, best_hash = 0, GENESIS
        for _, path in self.journal_files(key):
            last = _last_line(path)
            if not last:
                continue
            try:
                ev = json.loads(last)
            except ValueError:
                raise AuditGuardError(f"{path}: the last line is not valid JSON - the journal is damaged; "
                                      "run 'auditguard verify'")
            seq = ev.get("seq")
            if isinstance(seq, int) and seq > best_seq:
                best_seq, best_hash = seq, str(ev.get("hash") or "")
        return best_seq, best_hash

    # ----------------------------------------------------------------- route
    def route(self, explicit: Optional[str] = None) -> Tuple[str, Optional[str]]:
        """(sprint folder to append to, sealed sprint the append was redirected from)."""
        redirected = None
        if explicit:
            if explicit == UNASSIGNED:
                return UNASSIGNED, None
            if not self.sealed(explicit):
                return explicit, None
            redirected = explicit
        cur = self.register.current(today())
        if cur is None:
            return UNASSIGNED, redirected
        if self.sealed(cur["id"]):
            return UNASSIGNED, redirected or cur["id"]
        return cur["id"], redirected

    # ---------------------------------------------------------------- append
    def append(self, key: str, spec: Dict[str, Any], *, evidence: Iterable[Tuple[str, bytes, Optional[str]]] = (),
               sprint: Optional[str] = None) -> Dict[str, Any]:
        """Append one event to the chain `key`. `spec` holds the event fields; chain fields are added here."""
        target, redirected = self.route(sprint)
        with _Lock(self.root / STATE_REL / "locks" / f"{key}.lock"):
            seq, prev = self.head(key)
            self._check_tail(key, seq, prev)
            folder = self.sprints_dir / target / key
            refs = [self.store_evidence(folder, name, data, source) for name, data, source in evidence]
            event: Dict[str, Any] = {"v": SCHEMA_VERSION, "seq": seq + 1}
            at = _normalise_at(spec.get("at")) or now_iso()
            event["at"] = at
            recorded = now_iso()
            if spec.get("at") and abs(time_key(recorded) - time_key(at)) >= 1:
                event["recorded"] = recorded
            event["sprint"] = target
            event["feature"] = spec.get("feature")
            event["stage"] = spec.get("stage")
            event["kind"] = spec["kind"]
            for opt in ("command", "outcome"):
                if spec.get(opt) is not None:
                    event[opt] = spec[opt]
            event["actor"] = spec.get("actor") or {"type": "script", "id": "auditguard"}
            event["source"] = spec.get("source") or "cli"
            for opt in ("commit", "branch", "dirty"):
                if opt in spec:
                    event[opt] = spec[opt]
            for opt in ("artefacts", "changed", "worktree"):
                if spec.get(opt) is not None:
                    event[opt] = spec[opt]
            data = dict(spec.get("data") or {})
            if redirected:
                data["redirected_from"] = redirected
            event["data"] = data
            event["evidence"] = refs + list(spec.get("evidence_refs") or [])
            event["prev"] = prev
            event["hash"] = event_hash(event)
            path = self.journal_path(target, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            line = (journal_line(event) + "\n").encode("utf-8")
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o644)
            try:
                os.write(fd, line)
                try:
                    os.fsync(fd)
                except OSError:
                    pass
            finally:
                os.close(fd)
        self._chains.pop(key, None)
        return event

    def _check_tail(self, key: str, seq: int, prev: str) -> None:
        """Cheap tamper check before extending a chain: the last two events link and hash correctly."""
        if seq == 0:
            return
        tail = [e for e in self.load_chain(key, refresh=True) if isinstance(e.get("seq"), int) and e["seq"] >= seq - 1]
        by_seq = {e["seq"]: e for e in tail}
        last = by_seq.get(seq)
        if last is None or last.get("hash") != event_hash({k: v for k, v in last.items() if not k.startswith("_")}):
            raise AuditGuardError(f"the chain '{key}' is damaged at seq {seq} (hash does not match) - refusing to "
                                  "append; run 'auditguard verify'")
        before = by_seq.get(seq - 1)
        if seq > 1 and (before is None or last.get("prev") != before.get("hash")):
            raise AuditGuardError(f"the chain '{key}' is broken between seq {seq - 1} and {seq} - refusing to append; "
                                  "run 'auditguard verify'")

    # --------------------------------------------------------------- evidence
    def store_evidence(self, folder: Path, name: str, data: bytes, source: Optional[str]) -> Dict[str, Any]:
        normalised = normalise_content(data)
        digest = content_hash(data)
        max_kb = int(self.cfg.get("audit", "evidence", "max_file_kb") or 512)
        snapshot = bool(self.cfg.get("audit", "evidence", "snapshot", default=True)) and len(normalised) <= max_kb * 1024
        ref: Dict[str, Any] = {"name": safe_name(name), "sha256": digest, "size": len(normalised)}
        if snapshot:
            rel = f"evidence/{digest[:12]}-{safe_name(name)}"
            target = folder / rel
            if not target.is_file():
                write_bytes(target, normalised)
            ref["path"] = rel
        else:
            ref["path"] = None
        ref["source_path"] = source
        ref["snapshot"] = snapshot
        return ref

    def evidence_path(self, event: Dict[str, Any], ref: Dict[str, Any]) -> Optional[Path]:
        if not ref.get("path"):
            return None
        sprint = event.get("_sprint_dir") or event.get("sprint")
        key = Path(str(event.get("feature"))).name if event.get("feature") else PROJECT_KEY
        return self.sprints_dir / str(sprint) / key / ref["path"]


def _normalise_at(value: Any) -> Optional[str]:
    """An ISO timestamp in the offset of the recording clock (naive source times are local time)."""
    if not value:
        return None
    parsed = parse_time(value)
    if parsed is None:
        return None
    return parsed.astimezone(now().tzinfo).replace(microsecond=0).isoformat()


def _last_line(path: Path) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            block = min(size, 65536)
            while True:
                handle.seek(size - block)
                data = handle.read(block)
                lines = [ln for ln in data.split(b"\n") if ln.strip()]
                if len(lines) >= 2 or block >= size:
                    return lines[-1].decode("utf-8", errors="replace") if lines else ""
                block = min(size, block * 4)
    except OSError:
        return ""


class _Lock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: Optional[int] = None

    def __enter__(self) -> "_Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + LOCK_TIMEOUT
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > STALE_LOCK:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise AuditGuardError(f"the journal is locked by another auditGuard process ({self.path}); "
                                          "try again, or remove the lock file if no auditGuard is running")
                time.sleep(0.05)

    def __exit__(self, *exc: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except OSError:
            pass


def clean(event: Dict[str, Any]) -> Dict[str, Any]:
    """The event as stored (without the loader's private _keys)."""
    return {k: v for k, v in event.items() if not k.startswith("_")}


def load_state(root: Path, name: str, default: Any) -> Any:
    return read_json(root / STATE_REL / name, default)
