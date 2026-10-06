"""Verification against the golden sources (G1-G8) and anchoring in git.

The trail is a set of claims about git (the artefacts, the code, the history), the BA specification (the
handover pins), the standards lock, the decision ledger and the workflow runs. `verify --golden`
re-derives every claim from its source and labels each event:

    verified    the claim agrees with the golden source
    ephemeral   the recorded state never reached git (a working state that changed again before a commit)
    unanchored  the claim cannot be anchored yet (not pushed, not committed, no anchor note or tag)
    mismatch    the claim contradicts the golden source

Every finding carries the command that reproduces it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import yamlio
from .common import (AuditGuardError, canonical_json, content_hash, git, git_bytes, glob_match, is_git_repo, now_iso,
                     read_bytes, read_json, read_text, rel_path, sha256_text, short, time_key)
from .store import Store

LABEL_RANK = {"verified": 0, "unanchored": 1, "ephemeral": 2, "mismatch": 3}


def worst(*labels: Optional[str]) -> str:
    present = [l for l in labels if l]
    return max(present, key=lambda l: LABEL_RANK.get(l, 0)) if present else "verified"


class GitReader:
    """Blob contents through one `git cat-file --batch` process, cached by object name."""

    def __init__(self, root: Path):
        self.root = root
        self.proc: Optional[subprocess.Popen] = None
        self.cache: Dict[str, Optional[bytes]] = {}

    def _start(self) -> None:
        self.proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=str(self.root), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, spec: str) -> Optional[bytes]:
        if spec in self.cache:
            return self.cache[spec]
        if self.proc is None:
            self._start()
        assert self.proc is not None and self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(spec.encode("utf-8") + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().decode("utf-8", errors="replace").strip()
        if header.endswith("missing") or header.endswith("ambiguous") or not header:
            self.cache[spec] = None
            return None
        parts = header.split()
        size = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
        data = self.proc.stdout.read(size)
        self.proc.stdout.read(1)
        self.cache[spec] = data if parts[1] == "blob" else data
        return self.cache[spec]

    def blob_hash(self, commit: str, path: str) -> Optional[str]:
        data = self.read(f"{commit}:{path}")
        return content_hash(data) if data is not None else None

    def close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.stdin.close()  # type: ignore[union-attr]
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                self.proc.kill()
            self.proc = None


class Golden:
    def __init__(self, root: Path, cfg: Any, store: Optional[Store] = None, *, offline: bool = False):
        self.root = root
        self.cfg = cfg
        self.store = store or Store(root, cfg)
        self.offline = offline
        self.git_ok = is_git_repo(root)
        self.reader = GitReader(root) if self.git_ok else None
        self.remote = self.cfg.get("golden", "git", "remote") or "origin"
        self.base = self.cfg.get("golden", "git", "base") or "main"
        self.notes_ref = self.cfg.get("golden", "git", "notes_ref") or "refs/notes/auditguard"
        self._path_history: Dict[str, List[Tuple[str, str]]] = {}
        self._reach: Dict[str, str] = {}
        self.findings: List[Dict[str, Any]] = []
        self.labels: Dict[str, Dict[str, Any]] = {}
        self.counts: Dict[str, Dict[str, int]] = {}

    # ------------------------------------------------------------- helpers
    def count(self, check: str, label: str, n: int = 1) -> None:
        self.counts.setdefault(check, {})
        self.counts[check][label] = self.counts[check].get(label, 0) + n

    def label(self, ev: Dict[str, Any], check: str, label: str, **detail: Any) -> None:
        entry = self.labels.setdefault(ev["hash"], {"label": "verified", "checks": {}})
        entry["checks"][check] = label
        entry["label"] = worst(entry["label"], label)
        for k, v in detail.items():
            if v is not None:
                entry[k] = v

    def finding(self, check: str, label: str, reproduce: str, **detail: Any) -> None:
        self.findings.append({"check": check, "label": label, **{k: v for k, v in detail.items() if v is not None},
                              "reproduce": reproduce})

    def remote_refs(self) -> List[str]:
        code, out = git(self.root, "for-each-ref", "--format=%(refname)", f"refs/remotes/{self.remote}")
        return [l.strip() for l in out.split("\n") if l.strip() and not l.strip().endswith("/HEAD")] if code == 0 else []

    def reachability(self, commit: str) -> str:
        """verified (on a remote branch), unanchored (only on local branches), mismatch (missing or dangling)."""
        if commit in self._reach:
            return self._reach[commit]
        code, _ = git(self.root, "cat-file", "-e", f"{commit}^{{commit}}")
        if code != 0:
            self._reach[commit] = "mismatch"
            return "mismatch"
        code, out = git(self.root, "for-each-ref", "--contains", commit, "--format=%(refname)",
                        f"refs/remotes/{self.remote}")
        if code == 0 and out.strip():
            result = "verified"
        else:
            code, out = git(self.root, "for-each-ref", "--contains", commit, "--format=%(refname)", "refs/heads", "refs/tags")
            result = "unanchored" if code == 0 and out.strip() else "mismatch"
        self._reach[commit] = result
        return result

    def history(self, path: str) -> List[Tuple[str, str]]:
        """[(commit, content hash or '-' when deleted)] of every commit on any branch that touched the path."""
        if path not in self._path_history:
            code, out = git(self.root, "log", "--all", "--format=%H", "--", path)
            items = []
            if code == 0:
                for sha in [l.strip() for l in out.split("\n") if l.strip()]:
                    h = self.reader.blob_hash(sha, path) if self.reader else None
                    items.append((sha, h or "-"))
            self._path_history[path] = items
        return self._path_history[path]

    def anchored_at(self, path: str, digest: str) -> Optional[str]:
        for sha, h in reversed(self.history(path)):   # oldest first
            if h == digest:
                return sha
        return None

    # ------------------------------------------------------------------ G1
    def g1(self, events: List[Dict[str, Any]]) -> None:
        for ev in events:
            commit = ev.get("commit")
            if not commit:
                continue
            res = self.reachability(commit)
            self.count("G1", res)
            self.label(ev, "G1", res)
            if res == "mismatch":
                self.finding("G1", "mismatch", f"git cat-file -t {commit} && git branch -a --contains {commit}",
                             reason="commit_unreachable", event=short(ev["hash"]), commit=commit,
                             where=f"{ev['sprint']}/{_key(ev)} #{ev.get('seq')}")

    # ------------------------------------------------------------------ G2
    def g2(self, events: List[Dict[str, Any]]) -> None:
        for ev in events:
            claims: Dict[str, Optional[str]] = dict(ev.get("artefacts") or {})
            for change in ev.get("changed") or []:
                if change.get("path") and change["path"] not in claims and change.get("to"):
                    claims[change["path"]] = change.get("to")
            commit = ev.get("commit")
            if not claims or not commit:
                continue
            files: Dict[str, Dict[str, Any]] = {}
            labels = []
            for path, digest in sorted(claims.items()):
                if not digest:
                    continue
                at_commit = self.reader.blob_hash(commit, path) if self.reader else None
                if at_commit == digest:
                    files[path] = {"label": "verified", "anchored_at": commit}
                    labels.append("verified")
                    continue
                anchor = self.anchored_at(path, digest)
                if anchor:
                    files[path] = {"label": "verified", "anchored_at": anchor}
                    labels.append("verified")
                elif ev.get("dirty") is False and at_commit is not None:
                    files[path] = {"label": "mismatch", "recorded": digest, "golden": at_commit}
                    labels.append("mismatch")
                    self.finding("G2", "mismatch", f"git cat-file blob {commit[:12]}:{path} | sha256sum",
                                 event=short(ev["hash"]), where=f"{ev['sprint']}/{_key(ev)} #{ev.get('seq')}",
                                 path=path, recorded=short(digest), golden=short(at_commit))
                else:
                    files[path] = {"label": "ephemeral", "recorded": digest}
                    labels.append("ephemeral")
            label = worst(*labels)
            self.count("G2", label)
            self.label(ev, "G2", label, files=files, anchored_at=self.common_anchor(commit, claims, files))
            if label == "ephemeral":
                self.finding("G2", "ephemeral", f"git log --all --oneline -- {next(iter(p for p, f in files.items() if f['label'] == 'ephemeral'))}",
                             event=short(ev["hash"]), where=f"{ev['sprint']}/{_key(ev)} #{ev.get('seq')}",
                             paths=[p for p, f in files.items() if f["label"] == "ephemeral"][:10])

    def commit_time(self, sha: str) -> int:
        code, out = git(self.root, "show", "-s", "--format=%ct", sha)
        return int(out.strip()) if code == 0 and out.strip().isdigit() else 0

    def common_anchor(self, commit: str, claims: Dict[str, Optional[str]], files: Dict[str, Dict[str, Any]]) -> Optional[str]:
        """The commit where every recorded file has the recorded content (the state the event describes)."""
        candidates = {commit} | {f["anchored_at"] for f in files.values() if f.get("anchored_at")}
        for sha in sorted(candidates, key=self.commit_time):
            if all(not d or (self.reader and self.reader.blob_hash(sha, p) == d) for p, d in claims.items()):
                return sha
        return None

    # ------------------------------------------------------------------ G3
    def g3(self, events: List[Dict[str, Any]]) -> None:
        with_commit = [e for e in events if e.get("commit")]
        if not with_commit:
            self.counts["G3"] = {"skipped": 1}
            return
        earliest = min(with_commit, key=lambda e: time_key(e.get("at")))["commit"]
        refs = ["HEAD"]
        for cand in (self.base, f"{self.remote}/{self.base}"):
            code, _ = git(self.root, "rev-parse", "--verify", "--quiet", cand)
            if code == 0:
                refs.append(cand)
        explain = self.cfg.get("golden", "git", "explain_paths") or []
        exclude = list(self.cfg.get("golden", "git", "exclude_paths") or [])
        audit_rel = rel_path(self.cfg.audit_root, self.root)
        exclude.append(f"{audit_rel}/**")
        pathspec = [f":(glob){p}" for p in explain]
        code, out = git(self.root, "log", "--format=@@%H%x1f%an%x1f%cI%x1f%P%x1f%s", "--name-status", "--no-renames",
                        *refs, f"^{earliest}", "--", *pathspec, timeout=300)
        if code != 0:
            self.counts["G3"] = {"skipped": 1}
            return
        content_claims: Set[Tuple[str, Optional[str]]] = set()
        path_windows: List[Tuple[float, float, Set[str]]] = []
        merged: Set[str] = set()
        starts: Dict[str, Dict[str, Any]] = {}
        for ev in events:
            for path, digest in (ev.get("artefacts") or {}).items():
                content_claims.add((path, digest))
            for c in ev.get("changed") or []:
                content_claims.add((c.get("path"), c.get("to")))
            kind = ev.get("kind")
            if kind == "command.started":
                starts[ev["hash"]] = ev
            if kind == "commit.merged":
                merged.add((ev.get("data") or {}).get("commit"))
            if kind in ("command.finished", "command.abandoned"):
                start = starts.get((ev.get("data") or {}).get("started_event"))
                t0 = time_key(start.get("at")) if start else time_key((ev.get("data") or {}).get("started_at"))
                t1 = max(time_key(ev.get("recorded") or ev.get("at")), time_key(ev.get("at")))
                paths = {c.get("path") for c in ev.get("changed") or []}
                if t0 and paths:
                    path_windows.append((t0 - 5, t1 + 5, paths))
        commits = []
        for block in out.split("@@")[1:]:
            lines = [l for l in block.split("\n") if l.strip()]
            head = lines[0].split("\x1f")
            sha, author, date, parents = head[0], head[1], head[2], head[3]
            files = []
            for l in lines[1:]:
                parts = l.split("\t")
                if len(parts) >= 2:
                    files.append((parts[0][:1], parts[-1]))
            commits.append((sha, author, date, parents.split(), files))
        explained = unexplained = 0
        for sha, author, date, parents, files in commits:
            relevant = [(st, p) for st, p in files if glob_match(p, explain) and not glob_match(p, exclude)]
            if not relevant:
                continue
            if sha in merged:
                explained += 1
                continue
            when = time_key(date)
            missing = []
            for status, path in relevant:
                digest = None if status == "D" else (self.reader.blob_hash(sha, path) if self.reader else None)
                if (path, digest) in content_claims:
                    continue
                if any(t0 <= when <= t1 and path in paths for t0, t1, paths in path_windows):
                    continue
                missing.append(path)
            if missing:
                unexplained += 1
                self.finding("G3", "unexplained_commit", f"git show --stat {sha[:12]}", commit=sha, author=author,
                             date=date, files=missing[:20])
            else:
                explained += 1
        self.counts["G3"] = {"explained": explained, "unexplained": unexplained}

    # ------------------------------------------------------------------ G4
    def g4(self, events: List[Dict[str, Any]]) -> None:
        for ev in events:
            labels = []
            for ref in ev.get("evidence") or []:
                src = ref.get("source_path")
                if not src or not ref.get("sha256"):
                    continue
                commit = ev.get("commit")
                at_commit = self.reader.blob_hash(commit, src) if (self.reader and commit) else None
                if at_commit == ref["sha256"] or self.anchored_at(src, ref["sha256"]):
                    labels.append("verified")
                elif not self.history(src):
                    labels.append("unanchored")
                elif ev.get("dirty") is False and at_commit is not None:
                    labels.append("mismatch")
                    self.finding("G4", "mismatch", f"git cat-file blob {commit[:12]}:{src} | sha256sum",
                                 event=short(ev["hash"]), where=f"{ev['sprint']}/{_key(ev)} #{ev.get('seq')}", path=src)
                else:
                    labels.append("ephemeral")
            if labels:
                label = worst(*labels)
                self.count("G4", label)
                self.label(ev, "G4", label)

    # ------------------------------------------------------------------ G5
    def g5(self, events: List[Dict[str, Any]]) -> None:
        ledger_path = self.cfg.path(self.cfg.get("golden", "ledger"))
        ledger_hashes: Set[str] = set()
        ledger_ok = True
        if ledger_path and ledger_path.is_file():
            prev = "0" * 64
            for no, raw in enumerate(read_text(ledger_path).split("\n"), start=1):
                if not raw.strip():
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    ledger_ok = False
                    continue
                body = {k: v for k, v in entry.items() if k != "hash"}
                if entry.get("hash") != sha256_text(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                                               separators=(",", ":"), default=str)) \
                        or entry.get("prev") != prev:
                    ledger_ok = False
                    self.finding("G5", "mismatch", f"archiguard ledger verify   (line {no} of {rel_path(ledger_path, self.root)})",
                                 reason="ledger_chain_broken", line=no)
                prev = entry.get("hash") or ""
                ledger_hashes.add(entry.get("hash"))
        for ev in events:
            kind = ev.get("kind")
            data = ev.get("data") or {}
            label = None
            if kind == "design.signed" or (kind == "decision" and data.get("subject") == "design" and data.get("verdict") == "approve"):
                label = self.check_signoff(ev, data)
            elif kind and kind.startswith("waiver.") and data.get("source") == "ledger":
                h = (data.get("record") or {}).get("ledger_hash") or (data.get("before") or {}).get("ledger_hash")
                label = "verified" if (h in ledger_hashes and ledger_ok) else ("mismatch" if ledger_path and ledger_path.is_file() else "unanchored")
                if label == "mismatch":
                    self.finding("G5", "mismatch", f"grep -n '{data.get('id')}' {rel_path(ledger_path, self.root)}",
                                 reason="ledger_entry_changed", event=short(ev["hash"]), id=data.get("id"))
            elif kind == "decision" and str(data.get("subject", "")).startswith("ledger:"):
                h = data.get("ledger_hash")
                label = "verified" if (h in ledger_hashes and ledger_ok) else "mismatch"
                if label == "mismatch":
                    self.finding("G5", "mismatch", f"grep -n '{data.get('subject')[7:]}' {rel_path(ledger_path, self.root) if ledger_path else 'ledger'}",
                                 reason="ledger_entry_changed", event=short(ev["hash"]), subject=data.get("subject"))
            elif kind == "gate.verdict" and data.get("tool") == "archiguard" and (data.get("pins") or {}).get("lock"):
                label = self.check_lock(ev, data)
            elif kind == "decision" and str(data.get("subject", "")).startswith("gate:") and data.get("run_id"):
                runs = self.cfg.path(self.cfg.get("collectors", "workflow", "runs"))
                state = read_json(runs / str(data["run_id"]) / "state.json", None) if runs else None
                if not state:
                    label = "unanchored"
                else:
                    res = (state.get("step_results") or {}).get(str(data["subject"])[5:]) or {}
                    label = "verified" if (res.get("output") or {}).get("choice") == data.get("verdict") else "mismatch"
                    if label == "mismatch":
                        self.finding("G5", "mismatch", f"cat {rel_path(runs / str(data['run_id']) / 'state.json', self.root)}",
                                     reason="workflow_verdict_differs", event=short(ev["hash"]), subject=data.get("subject"))
            if label:
                self.count("G5", label)
                self.label(ev, "G5", label)

    def check_signoff(self, ev: Dict[str, Any], data: Dict[str, Any]) -> str:
        feature = ev.get("feature")
        if not feature:
            return "unanchored"
        labels = []
        hashes = data.get("hashes") or {}
        spec_hash = hashes.get("spec.md") or hashes.get(f"{feature}/spec.md")
        handover_name = self.cfg.get("golden", "handover") or "handover.yml"
        handover = self.root / feature / handover_name
        if spec_hash and handover.is_file():
            try:
                pinned = ((yamlio.load_file(handover).get("source") or {}).get("sha256"))
            except AuditGuardError:
                pinned = None
            if pinned and not str(pinned).startswith("<"):
                if pinned == spec_hash:
                    labels.append("verified")
                else:
                    labels.append("mismatch")
                    self.finding("G5", "mismatch", f"python -c \"...\"  # compare sha256 of {feature}/spec.md with source.sha256 in {feature}/{handover_name}",
                                 reason="signed_spec_differs_from_handover", event=short(ev["hash"]), feature=feature,
                                 signed=short(spec_hash), handover=short(pinned))
        fp = data.get("fingerprint")
        if fp and str(ev.get("source", "")).startswith("collector:archiguard"):
            found = False
            for path in (f"{feature}/gates/signoff.json",):
                for sha, _ in self.history(path):
                    blob = self.reader.read(f"{sha}:{path}") if self.reader else None
                    if blob is None:
                        continue
                    try:
                        rec = json.loads(blob.decode("utf-8"))
                    except ValueError:
                        continue
                    if content_hash(json.dumps({k: v for k, v in rec.items() if k != "reopened"}, sort_keys=True).encode()) == fp:
                        found = True
                        break
            labels.append("verified" if found else "unanchored")
        return worst(*labels) if labels else "verified"

    def check_lock(self, ev: Dict[str, Any], data: Dict[str, Any]) -> str:
        lock_rel = self.cfg.get("golden", "lock")
        if not lock_rel:
            return "unanchored"
        commit = ev.get("commit")
        candidates = []
        if commit and self.reader:
            blob = self.reader.read(f"{commit}:{lock_rel}")
            if blob is not None:
                candidates.append(blob)
        path = self.root / lock_rel
        if path.is_file():
            candidates.append(read_bytes(path))
        for sha, _ in self.history(lock_rel)[:20]:
            blob = self.reader.read(f"{sha}:{lock_rel}") if self.reader else None
            if blob is not None:
                candidates.append(blob)
        if not candidates:
            return "unanchored"
        want = data["pins"]["lock"]
        for blob in candidates:
            try:
                lock = yamlio.loads(blob.decode("utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if "sha256:" + sha256_text(canonical_json(lock)) == want:
                return "verified"
        self.finding("G5", "mismatch", f"git log --oneline -- {lock_rel}", reason="standards_lock_differs",
                     event=short(ev["hash"]), recorded=want)
        return "mismatch"

    # --------------------------------------------------------------- G6/G7
    def notes(self) -> List[Tuple[str, Dict[str, Any]]]:
        code, out = git(self.root, "notes", f"--ref={self.notes_ref}", "list")
        items = []
        if code != 0:
            return items
        for line in out.split("\n"):
            parts = line.split()
            if len(parts) != 2:
                continue
            blob = self.reader.read(parts[0]) if self.reader else None
            try:
                data = json.loads(blob.decode("utf-8")) if blob else None
            except ValueError:
                data = None
            if isinstance(data, dict):
                items.append((parts[1], data))
        return sorted(items, key=lambda x: time_key(x[1].get("at")))

    def g6(self, internal: Dict[str, Any]) -> None:
        sign = bool(self.cfg.get("golden", "git", "sign"))
        audit_rel = rel_path(self.cfg.audit_root, self.root)
        notes = self.notes()
        for sprint, seal in sorted((internal.get("seals") or {}).items()):
            tag = f"audit/{sprint}"
            code, kind = git(self.root, "cat-file", "-t", f"refs/tags/{tag}")
            if code != 0:
                self.count("G6", "unanchored")
                self.finding("G6", "unanchored", f"auditguard anchor --sprint {sprint} --push", sprint=sprint,
                             reason="no anchor tag")
                continue
            problems = []
            if kind.strip() != "tag":
                problems.append("the tag is not annotated")
            code, msg = git(self.root, "cat-file", "-p", f"refs/tags/{tag}")
            if seal.get("hash") not in msg:
                problems.append("the tag message does not carry the seal hash")
            seal_rel = f"{audit_rel}/sprints/{sprint}/seal.json"
            blob = self.reader.read(f"refs/tags/{tag}^{{commit}}:{seal_rel}") if self.reader else None
            current = self.cfg.audit_root / "sprints" / sprint / "seal.json"
            if blob is None:
                problems.append("the tagged commit does not contain seal.json")
            elif current.is_file() and content_hash(blob) != content_hash(read_bytes(current)):
                problems.append("seal.json differs from the tagged one")
            if sign:
                code, _ = git(self.root, "tag", "-v", tag)
                if code != 0:
                    problems.append("the tag signature does not verify")
            code, tagged = git(self.root, "rev-parse", f"refs/tags/{tag}^{{commit}}")
            note_ok = any(obj == tagged.strip() and (data.get("seals") or {}).get(sprint) == seal.get("hash")
                          for obj, data in notes)
            if not note_ok:
                problems.append("no anchor note on the tagged commit repeats the seal hash")
            if problems:
                self.count("G6", "mismatch")
                self.finding("G6", "mismatch", f"git tag -v {tag}; git show {tag} --stat", sprint=sprint,
                             reason="; ".join(problems))
            else:
                self.count("G6", "verified")

    def g7(self, events_by_chain: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        notes = self.notes()
        info: Dict[str, Any] = {"notes": len(notes), "latest": None, "pushed": None}
        if not notes:
            for key, events in events_by_chain.items():
                for ev in events:
                    self.label(ev, "G7", "unanchored")
                self.count("G7", "unanchored", len(events))
            return info
        obj, latest = notes[-1]
        info["latest"] = {"commit": obj, "at": latest.get("at"), "by": latest.get("by")}
        chains = latest.get("chains") or {}
        for key, events in events_by_chain.items():
            anchor = chains.get(key)
            by_seq = {e.get("seq"): e for e in events}
            if anchor is None:
                for ev in events:
                    self.label(ev, "G7", "unanchored")
                self.count("G7", "unanchored", len(events))
                continue
            at_seq = by_seq.get(anchor.get("seq"))
            if at_seq is None or at_seq.get("hash") != anchor.get("hash"):
                self.count("G7", "mismatch")
                self.finding("G7", "mismatch", f"git notes --ref={self.notes_ref} show {obj[:12]}", chain=key,
                             reason="rewritten_after_anchor", anchored_seq=anchor.get("seq"),
                             anchored_hash=short(anchor.get("hash")))
                for ev in events:
                    self.label(ev, "G7", "mismatch")
                continue
            for ev in events:
                self.label(ev, "G7", "verified" if ev.get("seq", 0) <= anchor.get("seq", 0) else "unanchored")
            self.count("G7", "verified", sum(1 for e in events if e.get("seq", 0) <= anchor.get("seq", 0)))
            self.count("G7", "unanchored", sum(1 for e in events if e.get("seq", 0) > anchor.get("seq", 0)))
        if not self.offline:
            code, out = git(self.root, "ls-remote", self.remote, self.notes_ref, timeout=20)
            code_l, local = git(self.root, "rev-parse", self.notes_ref)
            if code == 0 and code_l == 0:
                info["pushed"] = bool(out.strip()) and out.split()[0] == local.strip()
        return info

    # ------------------------------------------------------------------ G8
    def g8(self, events: List[Dict[str, Any]]) -> None:
        script = self.root / ".specify" / "extensions" / "scopeguard" / "scripts" / "python" / "scopeguard.py"
        if not script.is_file():
            self.counts["G8"] = {"skipped": 1}
            return
        todo = []
        for idx, ev in enumerate(events):
            data = ev.get("data") or {}
            if ev.get("kind") != "gate.verdict" or data.get("tool") != "scopeguard":
                continue
            # the artefacts the report was computed from: the next record of the chain that hashes them
            # (normally the command.finished of the same command), anchored in git by G2
            nxt = next((e for e in events[idx + 1:] if e.get("feature") == ev.get("feature") and e.get("artefacts")), None)
            lab = (self.labels.get(nxt["hash"]) or {}) if nxt else {}
            anchor = lab.get("anchored_at") if lab.get("checks", {}).get("G2") == "verified" else None
            ref = next((r for r in ev.get("evidence") or [] if r.get("name") == "scopeguard-report.json"), None)
            if anchor and ref and ref.get("path"):
                todo.append((ev, anchor, ref))
        for ev, anchor, ref in todo:
            snap_path = self.store.evidence_path(ev, ref)
            if snap_path is None or not snap_path.is_file():
                continue
            with tempfile.TemporaryDirectory() as tmp:
                wt = Path(tmp) / "wt"
                code, _ = git(self.root, "worktree", "add", "--detach", "--quiet", str(wt), anchor, timeout=120)
                if code != 0:
                    self.count("G8", "skipped")
                    continue
                try:
                    proc = subprocess.run([sys.executable, str(script), "report", "--json", "--root", str(wt),
                                           "--feature-dir", str(ev.get("feature"))], cwd=str(wt), capture_output=True,
                                          timeout=300)
                    fresh = json.loads(proc.stdout.decode("utf-8", errors="replace"))
                finally:
                    git(self.root, "worktree", "remove", "--force", str(wt))
            feat = next((f for f in fresh.get("features") or [] if Path(str(f.get("feature_dir"))).name == Path(str(ev.get("feature"))).name), None)
            snap = json.loads(read_text(snap_path))
            phase = (ev.get("data") or {}).get("gate")
            same = feat is not None and (feat.get("summary") or {}).get(phase) == (snap.get("summary") or {}).get(phase)
            label = "verified" if same else "mismatch"
            self.count("G8", label)
            self.label(ev, "G8", label)
            if not same:
                self.finding("G8", "mismatch", f"git worktree add /tmp/g8 {anchor[:12]} && python {rel_path(script, self.root)} report --root /tmp/g8 --feature-dir {ev.get('feature')}",
                             event=short(ev["hash"]), reason="the recomputed scopeGuard report differs", gate=phase)

    # ---------------------------------------------------------------- run
    def run(self, internal: Dict[str, Any], *, recompute: bool = False, sprints: Optional[List[str]] = None) -> Dict[str, Any]:
        if not self.git_ok:
            from .common import git_toplevel
            top = git_toplevel(self.root)
            reason = ("the Spec Kit project is not the root of its git repository " + f"({top})" if top
                      else "not a git repository")
            return {"status": "skipped", "reason": reason, "checks": {}, "events": {}, "findings": []}
        by_chain: Dict[str, List[Dict[str, Any]]] = {}
        events: List[Dict[str, Any]] = []
        for key in self.store.chain_keys():
            chain = [e for e in self.store.load_chain(key)]
            by_chain[key] = chain
            events.extend(chain)
        scoped = [e for e in events if sprints is None or e.get("_sprint_dir") in sprints]
        try:
            self.g1(scoped)
            self.g2(scoped)
            self.g3(events)
            self.g4(scoped)
            self.g5(scoped)
            self.g6(internal)
            scoped_ids = {id(e) for e in scoped}
            anchors = self.g7({k: [e for e in v if id(e) in scoped_ids] for k, v in by_chain.items()})
            if recompute:
                self.g8(scoped)
            else:
                self.counts["G8"] = {"skipped": 1}
        finally:
            if self.reader:
                self.reader.close()
        for ev in scoped:
            self.labels.setdefault(ev["hash"], {"label": "verified", "checks": {}})
        failing = [f for f in self.findings if f["label"] in ("mismatch", "unexplained_commit")]
        sources = {"git": "ok", "handover": "ok" if any((self.root / str(e.get("feature")) / (self.cfg.get("golden", "handover") or "handover.yml")).is_file()
                                                        for e in scoped if e.get("feature")) else "not present",
                   "lock": "ok" if self.cfg.path(self.cfg.get("golden", "lock")) and self.cfg.path(self.cfg.get("golden", "lock")).is_file() else "not present",
                   "ledger": "ok" if self.cfg.path(self.cfg.get("golden", "ledger")) and self.cfg.path(self.cfg.get("golden", "ledger")).is_file() else "not present",
                   "workflow": "ok" if (self.cfg.path(self.cfg.get("collectors", "workflow", "runs")) or Path("/nonexistent")).is_dir() else "not present",
                   "tracker": "configured" if self.cfg.get("golden", "tracker") else "not configured"}
        return {"status": "fail" if failing else "pass", "sources": sources, "checks": self.counts,
                "anchors": anchors, "events": self.labels, "findings": self.findings}


def _key(ev: Dict[str, Any]) -> str:
    return Path(str(ev.get("feature"))).name if ev.get("feature") else "_project"


# --------------------------------------------------------------------------- #
# anchor                                                                        #
# --------------------------------------------------------------------------- #


def anchor(root: Path, cfg: Any, *, sprint: Optional[str], push: bool, by: str) -> Tuple[Dict[str, Any], List[str]]:
    if not is_git_repo(root):
        raise AuditGuardError("anchoring needs a git repository")
    store = Store(root, cfg)
    notes_ref = cfg.get("golden", "git", "notes_ref") or "refs/notes/auditguard"
    remote = cfg.get("golden", "git", "remote") or "origin"
    audit_rel = rel_path(cfg.audit_root, root)
    messages: List[str] = []
    code, head = git(root, "rev-parse", "HEAD")
    if code != 0:
        raise AuditGuardError("the repository has no commit yet")
    head = head.strip()
    code, status = git(root, "status", "--porcelain", "--", audit_rel)
    if status.strip():
        messages.append(f"WARNING: {audit_rel}/ has uncommitted changes - commit them so the anchored trail is in git")
    chains = {}
    journals = {}
    for key in store.chain_keys():
        chain = store.load_chain(key)
        if chain:
            chains[key] = {"seq": chain[-1].get("seq"), "hash": chain[-1].get("hash")}
        for sprint_dir, path in store.journal_files(key):
            evs, _ = store.read_journal(path)
            if evs:
                journals[rel_path(path, root)] = {"hash": evs[-1].get("hash"), "seq": evs[-1].get("seq")}
    seals = {}
    for sprint_dir in store.sprint_dirs():
        seal = read_json(store.sprints_dir / sprint_dir / "seal.json", None)
        if isinstance(seal, dict):
            seals[sprint_dir] = seal.get("hash")
    note = {"v": 1, "at": now_iso(), "by": by, "commit": head, "chains": chains, "journals": journals, "seals": seals}
    if sprint:
        if sprint not in seals:
            raise AuditGuardError(f"sprint {sprint} is not sealed - close it first (auditguard sprint close {sprint} --by <name>)")
        seal_rel = f"{audit_rel}/sprints/{sprint}/seal.json"
        code, blob = git_bytes(root, "show", f"HEAD:{seal_rel}")
        current = read_bytes(store.sprints_dir / sprint / "seal.json")
        if code != 0 or content_hash(blob) != content_hash(current):
            raise AuditGuardError(f"HEAD does not contain {seal_rel} as sealed - commit the audit folder first: "
                                  f"git add {audit_rel} && git commit -m \"Close sprint {sprint}\"")
    code, out = git(root, "notes", f"--ref={notes_ref}", "add", "-f", "-m", json.dumps(note, sort_keys=True), head)
    if code != 0:
        raise AuditGuardError(f"git notes failed: {out.strip()}")
    messages.append(f"anchor note on {head[:12]} ({notes_ref}): {len(chains)} chain head(s), {len(seals)} seal(s)")
    tag = None
    if sprint:
        tag = f"audit/{sprint}"
        args = ["tag", "-f", "-s" if cfg.get("golden", "git", "sign") else "-a", tag, "-m",
                f"auditGuard seal {sprint} {seals[sprint]}", head]
        code, out = git(root, *args)
        if code != 0:
            raise AuditGuardError(f"git tag failed: {out.strip()}")
        messages.append(f"tag {tag} -> {head[:12]}" + (" (signed)" if cfg.get("golden", "git", "sign") else ""))
    if push:
        code, out = git(root, "push", remote, notes_ref, timeout=120)
        messages.append(f"push {notes_ref}: {'ok' if code == 0 else 'FAILED - ' + out.strip()[-200:]}")
        if tag:
            code, out = git(root, "push", "-f", remote, f"refs/tags/{tag}", timeout=120)
            messages.append(f"push {tag}: {'ok' if code == 0 else 'FAILED - ' + out.strip()[-200:]}")
    return note, messages


def tracker_check(root: Path, cfg: Any, events: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """FR-611: the plug-in tracker check: `<command> --check <json of issue keys>` prints {key: true|false}."""
    tracker = cfg.get("golden", "tracker")
    if not isinstance(tracker, dict) or not tracker.get("command"):
        return None
    keys = sorted({str(k) for e in events for k in ((e.get("data") or {}).get("issues") or [])})
    from .collectors.plugged import _argv
    argv = _argv(str(tracker["command"]), root) + ["--check", json.dumps(keys)]
    try:
        proc = subprocess.run(argv, cwd=str(root), capture_output=True, timeout=int(tracker.get("timeout") or 120))
        result = json.loads(proc.stdout.decode("utf-8") or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    missing = [k for k, ok in result.items() if not ok] if isinstance(result, dict) else []
    return {"status": "pass" if not missing else "fail", "checked": len(keys), "missing": missing}


def render(result: Dict[str, Any]) -> List[str]:
    lines = []
    if result.get("status") == "skipped":
        return [f"golden   : SKIPPED ({result.get('reason')})"]
    src = result.get("sources") or {}
    lines.append(f"golden   : {result['status'].upper():<6} " + " · ".join(f"{k} {v}" for k, v in src.items()))
    names = {"G1": "commits reachable", "G2": "artefact hashes", "G3": "commits explained", "G4": "evidence authentic",
             "G5": "decisions vs sources", "G6": "seals anchored", "G7": "chain heads anchored", "G8": "recompute"}
    for check in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"):
        counts = (result.get("checks") or {}).get(check) or {}
        text = " · ".join(f"{v} {k}" for k, v in counts.items()) or "nothing to check"
        if counts.get("skipped") and len(counts) == 1:
            text = "skipped" + (" (use --recompute)" if check == "G8" else "")
        lines.append(f"  {check} {names[check]:<22} {text}")
    findings = [f for f in result.get("findings") or [] if f["label"] != "ephemeral"]
    eph = [f for f in result.get("findings") or [] if f["label"] == "ephemeral"]
    if findings:
        lines.append("")
        lines.append("FINDINGS")
        for i, f in enumerate(findings, start=1):
            detail = " ".join(f"{k}={v if not isinstance(v, list) else ','.join(map(str, v))}" for k, v in f.items()
                              if k not in ("check", "label", "reproduce"))
            lines.append(f"  {i}. [{f['check']}] {f['label']}  {detail}")
            lines.append(f"        reproduce: {f['reproduce']}")
    if eph:
        lines.append(f"  ({len(eph)} ephemeral event(s): recorded working states that never reached git - informational)")
    return lines
