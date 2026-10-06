"""Shared fixtures: a throw-away Spec Kit project in a git repository, driven through the real CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "scripts" / "python" / "auditguard.py"
sys.path.insert(0, str(REPO / "scripts" / "python"))

CLEAN_ENV = ("AUDITGUARD_ACTOR", "AUDITGUARD_CONTEXT", "AUDITGUARD_MODE", "AUDITGUARD_INTEGRATION", "SPECIFY_FEATURE_DIRECTORY",
             "CI", "GITHUB_ACTIONS", "AUDITGUARD_CI", "GITHUB_ACTOR")


class Project:
    def __init__(self, root: Path):
        self.root = root
        self.now = "2026-10-06T09:00:00+02:00"

    # --------------------------------------------------------------- running
    def env(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV}
        env.update({"AUDITGUARD_NOW": self.now, "GIT_AUTHOR_DATE": self.now, "GIT_COMMITTER_DATE": self.now,
                    "PYTHONDONTWRITEBYTECODE": "1"})
        env.update(extra or {})
        return env

    def ag(self, *args: str, code: Optional[int] = 0, stdin: Optional[str] = None, **env: str) -> subprocess.CompletedProcess:
        proc = subprocess.run([sys.executable, str(ENGINE), *args], cwd=str(self.root), env=self.env(env),
                              capture_output=True, text=True, input=stdin, encoding="utf-8")
        if code is not None and proc.returncode != code:
            raise AssertionError(f"auditguard {' '.join(args)} exited {proc.returncode}, wanted {code}\n"
                                 f"--- stdout\n{proc.stdout}\n--- stderr\n{proc.stderr}")
        return proc

    def hook(self, event: str, feature: Optional[str] = None, **env: str) -> subprocess.CompletedProcess:
        args = ["hook", event] + (["--feature-dir", feature] if feature else [])
        return self.ag(*args, AUDITGUARD_CONTEXT="agent", **env)

    def event(self, name: str, payload: dict, code: int = 0) -> subprocess.CompletedProcess:
        return self.ag("event", name, stdin=json.dumps(payload), code=code)

    def git(self, *args: str, author: str = "Roman <roman@example.com>") -> str:
        name, mail = author.split(" <")
        proc = subprocess.run(["git", "-c", f"user.name={name}", "-c", f"user.email={mail.rstrip('>')}", *args],
                              cwd=str(self.root), env=self.env(), capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(f"git {' '.join(args)}: {proc.stderr}")
        return proc.stdout

    def commit(self, message: str, author: str = "Roman <roman@example.com>") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, author=author)
        return self.git("rev-parse", "HEAD").strip()

    # ----------------------------------------------------------------- files
    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def append(self, rel: str, text: str) -> None:
        with open(self.root / rel, "a", encoding="utf-8") as handle:
            handle.write(text)

    def feature(self, name: str = "001-place-order", spec: str = "# Spec\n\n### User Story 1 - Place an order (Priority: P1)\n") -> str:
        rel = f"specs/{name}"
        self.write(f"{rel}/spec.md", spec)
        self.write(".specify/feature.json", json.dumps({"feature_directory": rel}))
        return rel

    # ----------------------------------------------------------------- trail
    def events(self, key: str = "001-place-order") -> List[dict]:
        out = []
        for path in sorted((self.root / "audit" / "sprints").glob(f"*/{key}/journal.jsonl")):
            out += [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return sorted(out, key=lambda e: e["seq"])

    def kinds(self, key: str = "001-place-order") -> List[str]:
        return [e["kind"] for e in self.events(key)]

    def open_sprint(self, sid: str = "S-1", start: str = "2026-10-01", end: str = "2026-10-31") -> None:
        self.ag("sprint", "open", sid, "--start", start, "--end", end, "--by", "Roman")


@pytest.fixture
def project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    root.mkdir()
    p = Project(root)
    p.git("init", "-q", "-b", "main")
    p.git("config", "commit.gpgsign", "false")
    p.git("config", "tag.gpgsign", "false")
    p.write(".specify/init-options.json", json.dumps({"ai": "claude", "script": "sh"}))
    p.write(".specify/memory/constitution.md", "# Constitution\n")
    p.write("src/app.py", "print('hello')\n")
    p.write(".gitignore", ".specify/auditguard/state/\n")
    p.ag("configure")
    p.commit("base")
    return p


@pytest.fixture
def opened(project: Project) -> Project:
    project.open_sprint()
    return project


def archiguard_verdict(status: str, step: str = "b", command: str = "speckit.plan", findings: int = 0) -> dict:
    """A combined step verdict in archiGuard's format (gates/<command>-<step>.json)."""
    short = command.replace("speckit.", "")
    return {"tool": "archiguard", "version": "0.1.0", "command": command, "step": step, "run": "step", "mode": "enforce",
            "feature": "specs/001-place-order", "commit": None, "dirty": True, "artefacts": {}, "pins": {"lock": None},
            "iteration": 1, "max_iterations": 3, "status": status,
            "escalation": None, "gates": [{"gate": "A3", "check": "A3.3", "name": "check-plan", "mode": "enforce", "repair": True,
                                           "status": "violation" if findings else "pass", "blocking": findings, "advisory": 0,
                                           "waived": 0, "error": None, "verdict": "gates/A3/A3.3.json"}],
            "findings": [{"rule": "ARCH-201", "message": "missing", "severity": "blocking"}] * findings,
            "history": [{"iteration": 0, "status": "violation"}, {"iteration": 1, "status": status}],
            "_name": f"{short}-{step}.json"}
