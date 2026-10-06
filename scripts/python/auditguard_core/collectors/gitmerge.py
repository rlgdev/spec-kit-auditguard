"""git collector: merge commits into the base branch that touch the feature (commit.merged)."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ..common import git
from .base import Emit, every

PR_RE = re.compile(r"(?:pull request|PR|!)\s*#?(\d+)", re.I)


def base_ref(rc: Any) -> str:
    base = rc.cfg.get("golden", "git", "base") or "main"
    remote = rc.cfg.get("golden", "git", "remote") or "origin"
    code, _ = git(rc.root, "rev-parse", "--verify", "--quiet", f"refs/heads/{base}")
    if code == 0:
        return base
    code, _ = git(rc.root, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{base}")
    return f"{remote}/{base}" if code == 0 else ""


def collect(rc: Any, feature: str, chain: List[Dict[str, Any]]) -> List[Emit]:
    if not rc.has_git:
        return []
    ref = base_ref(rc)
    if not ref:
        return []
    code, out = git(rc.root, "log", "--merges", "--first-parent", ref, "--format=%H%x1f%an%x1f%aI%x1f%s", "--", feature)
    if code != 0:
        return []
    known = {(ev.get("data") or {}).get("commit") for ev in every(chain, ["commit.merged"])}
    emits: List[Emit] = []
    for line in reversed([ln for ln in out.split("\n") if ln.strip()]):
        parts = line.split("\x1f")
        if len(parts) < 4 or parts[0] in known:
            continue
        sha, author, date, subject = parts[:4]
        code, files = git(rc.root, "diff", "--name-only", f"{sha}^1", sha, "--")
        m = PR_RE.search(subject)
        emits.append(Emit(feature, {"kind": "commit.merged", "at": date, "actor": rc.actor_script("git"),
                                    "source": "collector:git",
                                    "data": {"commit": sha, "author": author, "date": date, "subject": subject,
                                             "pr": m.group(1) if m else None, "base": ref,
                                             "files": [f for f in files.split("\n") if f.strip()][:500] if code == 0 else []}}))
    return emits
