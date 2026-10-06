"""Run context: git state, artefact hashes, code changes, actors and the workstation state files."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import (PROJECT_KEY, SPECIFY_DIR, STATE_REL, content_hash, file_hash, git, git_bytes, git_branch,
                     git_head, git_user, glob_match, is_ci, is_git_repo, read_json, rel_path, write_json)
from .config import Config
from .store import Store


class RunContext:
    def __init__(self, root: Path, cfg: Config, store: Optional[Store] = None):
        self.root = root
        self.cfg = cfg
        self.store = store or Store(root, cfg)
        self.has_git = is_git_repo(root) and bool(cfg.get("collectors", "git", "enabled", default=True))
        self._head: Optional[str] = None
        self._head_done = False
        self._branch: Optional[str] = None
        self._dirty: Optional[Dict[str, Optional[str]]] = None
        self._blob_cache: Dict[Tuple[str, str], Optional[str]] = {}
        self.state_dir = root / STATE_REL

    # ------------------------------------------------------------------ git
    @property
    def head(self) -> Optional[str]:
        if not self._head_done:
            self._head = git_head(self.root) if self.has_git else None
            self._head_done = True
        return self._head

    @property
    def branch(self) -> Optional[str]:
        if self._branch is None:
            self._branch = git_branch(self.root) if self.has_git else ""
        return self._branch or None

    def invalidate(self) -> None:
        self._head_done = False
        self._branch = None
        self._dirty = None

    def excluded(self, rel: str) -> bool:
        audit_rel = rel_path(self.cfg.audit_root, self.root)
        if rel == audit_rel or rel.startswith(audit_rel.rstrip("/") + "/"):
            return True
        if rel.startswith(STATE_REL.as_posix() + "/") or rel.startswith((SPECIFY_DIR / "auditguard").as_posix() + "/"):
            return True
        return glob_match(rel, self.cfg.get("golden", "git", "exclude_paths") or [])

    def tracked(self, rel: str) -> bool:
        return glob_match(rel, self.cfg.get("golden", "git", "explain_paths") or []) and not self.excluded(rel)

    def is_code(self, rel: str) -> bool:
        """A tracked path outside the feature design artefacts and the project artefacts."""
        if rel.startswith("specs/"):
            return False
        if glob_match(rel, self.cfg.get("collectors", "git", "project_artefacts") or []):
            return False
        return self.tracked(rel)

    def dirty_map(self) -> Dict[str, Optional[str]]:
        """{path: content hash (None = deleted)} of the tracked files that differ from HEAD (incl. untracked)."""
        if self._dirty is not None:
            return self._dirty
        out: Dict[str, Optional[str]] = {}
        if self.has_git:
            code, raw = git_bytes(self.root, "status", "--porcelain", "-uall", "-z")
            if code == 0:
                items = raw.decode("utf-8", errors="replace").split("\0")
                i = 0
                while i < len(items):
                    item = items[i]
                    i += 1
                    if len(item) < 4:
                        continue
                    status, path = item[:2], item[3:]
                    if status[0] in ("R", "C"):
                        i += 1  # the source path of a rename follows
                    if not self.tracked(path):
                        continue
                    out[path] = file_hash(self.root / path)
        self._dirty = out
        return out

    @property
    def dirty(self) -> bool:
        return bool(self.dirty_map())

    def git_fields(self) -> Dict[str, Any]:
        if not self.has_git:
            return {"commit": None, "branch": None, "dirty": None}
        return {"commit": self.head, "branch": self.branch, "dirty": self.dirty}

    def blob_hash(self, commit: Optional[str], rel: str) -> Optional[str]:
        """Content hash of a file in a commit (None when absent)."""
        if not commit or not self.has_git:
            return None
        key = (commit, rel)
        if key not in self._blob_cache:
            code, data = git_bytes(self.root, "cat-file", "blob", f"{commit}:{rel}")
            self._blob_cache[key] = content_hash(data) if code == 0 else None
        return self._blob_cache[key]

    def worktree_code(self) -> Dict[str, Optional[str]]:
        return {p: h for p, h in self.dirty_map().items() if self.is_code(p)}

    def code_changes(self, base_commit: Optional[str], base_worktree: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
        """Code files whose content differs now from the recorded base state (commit + dirty files)."""
        if not self.has_git or not base_commit:
            return []
        candidates = set(base_worktree)
        code, out = git(self.root, "diff", "--name-only", "--no-renames", base_commit, "--")
        if code == 0:
            candidates.update(line.strip() for line in out.split("\n") if line.strip())
        candidates.update(self.dirty_map())
        changes = []
        for rel in sorted(candidates):
            if not self.is_code(rel):
                continue
            current = file_hash(self.root / rel)
            before = base_worktree[rel] if rel in base_worktree else self.blob_hash(base_commit, rel)
            if current != before:
                changes.append({"path": rel, "from": before, "to": current})
        return changes

    # ------------------------------------------------------------ artefacts
    def artefact_hashes(self, feature_dir: Optional[Path]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if feature_dir is None:
            for pattern in self.cfg.get("collectors", "git", "project_artefacts") or []:
                for rel in _glob_files(self.root, pattern):
                    h = file_hash(self.root / rel)
                    if h:
                        out[rel] = h
            return out
        base = rel_path(feature_dir, self.root)
        patterns = self.cfg.get("collectors", "git", "artefacts") or []
        if feature_dir.is_dir():
            for path in sorted(feature_dir.rglob("*")):
                if not path.is_file():
                    continue
                inner = path.relative_to(feature_dir).as_posix()
                if glob_match(inner, patterns):
                    h = file_hash(path)
                    if h:
                        out[f"{base}/{inner}"] = h
        return out

    # --------------------------------------------------------------- actors
    def agent_id(self) -> str:
        conf = self.cfg.get("actors", "agent")
        if conf and conf != "auto":
            return str(conf)
        opts = read_json(self.root / SPECIFY_DIR / "init-options.json", {}) or {}
        for key in ("ai", "integration", "agent"):
            if isinstance(opts.get(key), str) and opts[key]:
                return opts[key]
        return "agent"

    def ci_id(self) -> str:
        for var in self.cfg.get("actors", "ci_env") or []:
            if os.environ.get(var):
                return os.environ[var]
        return "ci"

    def actor_agent(self) -> Dict[str, Any]:
        if is_ci():
            return {"type": "ci", "id": self.ci_id()}
        actor: Dict[str, Any] = {"type": "agent", "id": self.agent_id()}
        session = self.session_id()
        if session:
            actor["session"] = session
        return actor

    def actor_human(self, by: Optional[str], role: Optional[str] = None) -> Dict[str, Any]:
        conf = self.cfg.get("actors", "human")
        name = (by or os.environ.get("AUDITGUARD_ACTOR") or (conf if conf and conf != "auto" else None)
                or git_user(self.root) or "unknown")
        actor: Dict[str, Any] = {"type": "human", "id": name.strip()}
        if role:
            actor["role"] = role.strip()
        if is_ci():
            actor["via"] = f"ci:{self.ci_id()}"
        return actor

    @staticmethod
    def actor_script(name: str = "auditguard") -> Dict[str, Any]:
        return {"type": "script", "id": name}

    # ---------------------------------------------------------- state files
    def state(self, name: str, default: Any) -> Any:
        return read_json(self.state_dir / name, default)

    def save_state(self, name: str, data: Any) -> None:
        write_json(self.state_dir / name, data)
        gi = self.state_dir.parent / ".gitignore"
        if not gi.is_file():
            gi.parent.mkdir(parents=True, exist_ok=True)
            gi.write_text("# auditGuard workstation state (open commands, session, locks) - not part of the audit trail\nstate/\n",
                          encoding="utf-8")

    def session_id(self) -> Optional[str]:
        if not self.cfg.get("sessions", "record", default=True):
            return None
        data = self.state("session.json", {}) or {}
        return data.get("session") if data.get("open") else None


def _glob_files(root: Path, pattern: str) -> List[str]:
    pattern = pattern.replace("\\", "/")
    if not any(ch in pattern for ch in "*?[{"):
        return [pattern] if (root / pattern).is_file() else []
    base = pattern.split("*")[0].rsplit("/", 1)[0] if "/" in pattern.split("*")[0] else ""
    start = root / base if base else root
    out = []
    if start.is_dir():
        for path in start.rglob("*"):
            if path.is_file():
                rel = path.relative_to(root).as_posix()
                if glob_match(rel, [pattern]):
                    out.append(rel)
    return sorted(out)


def key_of(feature: Optional[str]) -> str:
    return Path(feature).name if feature else PROJECT_KEY


def dumps_payload(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)
