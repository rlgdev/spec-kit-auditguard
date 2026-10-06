"""Out-of-band change detection: artefact and code changes that no recorded command explains (artefact.changed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .common import git, glob_match, now_iso, rel_path
from .context import key_of

SIGNED_PATTERNS = ["plan.md", "research.md", "data-model.md", "quickstart.md", "contracts/**"]
HANDOVER_PATTERNS = ["spec.md", "handover.yml", "ba/**"]


def last_artefacts(chain: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    for ev in reversed(chain):
        if isinstance(ev.get("artefacts"), dict):
            return ev["artefacts"]
    return None


def diff_artefacts(before: Dict[str, str], now: Dict[str, str]) -> List[Dict[str, Any]]:
    out = []
    for path in sorted(set(before) | set(now)):
        if before.get(path) != now.get(path):
            out.append({"path": path, "from": before.get(path), "to": now.get(path)})
    return out


def design_signed(rc: Any, feature_dir: Optional[Path], chain: List[Dict[str, Any]]) -> bool:
    if feature_dir is not None and (feature_dir / "gates" / "signoff.json").is_file():
        return True
    signed = False
    for ev in chain:
        if ev.get("kind") == "design.signed":
            signed = True
        elif ev.get("kind") == "design.reopened":
            signed = False
    return signed


def flags(rc: Any, feature_dir: Optional[Path], chain: List[Dict[str, Any]], changes: List[Dict[str, Any]]) -> Dict[str, Any]:
    if feature_dir is None:
        return {"after_signoff": False, "after_handover": False}
    base = rel_path(feature_dir, rc.root)
    inner = [c["path"][len(base) + 1:] for c in changes if c["path"].startswith(base + "/")]
    signed = design_signed(rc, feature_dir, chain)
    handed = (feature_dir / str(rc.cfg.get("golden", "handover") or "handover.yml")).is_file()
    return {
        "after_signoff": bool(signed and any(glob_match(p, SIGNED_PATTERNS) for p in inner)),
        "after_handover": bool(handed and any(glob_match(p, HANDOVER_PATTERNS) for p in inner)),
    }


def infer_command(rc: Any, feature_dir: Optional[Path], changes: List[Dict[str, Any]]) -> Optional[str]:
    if not changes:
        return None
    base = rel_path(feature_dir, rc.root) + "/" if feature_dir is not None else "\0"
    inner = {c["path"][len(base):] for c in changes if c["path"].startswith(base)}
    code = [c for c in changes if rc.is_code(c["path"])]
    if code and not inner:
        return "speckit.implement"
    if inner == {"spec.md"}:
        return "speckit.specify|speckit.clarify"
    if "plan.md" in inner and inner <= {"plan.md", "research.md", "data-model.md", "quickstart.md"} | {
            p for p in inner if p.startswith("contracts/")}:
        return "speckit.plan"
    if inner == {"tasks.md"}:
        return "speckit.tasks" if not code else "speckit.implement"
    if inner and all(p.startswith("checklists/") for p in inner):
        return "speckit.checklist"
    if any(c["path"].endswith(".specify/memory/constitution.md") for c in changes):
        return "speckit.constitution"
    return None


def authors(rc: Any, changes: List[Dict[str, Any]]) -> Dict[str, str]:
    """The author of the committed version, for changed files whose current content is committed."""
    out: Dict[str, str] = {}
    if not rc.has_git:
        return out
    dirty = rc.dirty_map()
    for c in changes[:50]:
        path = c["path"]
        if path in dirty or c.get("to") is None:
            continue
        code, line = git(rc.root, "log", "-1", "--format=%an%x1f%h", "--", path)
        if code == 0 and line.strip():
            name, sha = (line.strip().split("\x1f") + [""])[:2]
            out[path] = f"{name} ({sha})"
    return out


def reconcile(rec: Any, feature_dir: Optional[Path], *, include_code: bool = True) -> Optional[Dict[str, Any]]:
    """Append one artefact.changed when the feature's artefacts (or the code) moved since the last record."""
    rc = rec.rc
    feature = rec.feature_path(feature_dir)
    chain = rec.chain(key_of(feature))
    before = last_artefacts(chain)
    now_hashes = rc.artefact_hashes(feature_dir)
    changes: List[Dict[str, Any]] = diff_artefacts(before, now_hashes) if before is not None else []
    if include_code:
        baseline = rc.state("baseline.json", {}) or {}
        if baseline.get("commit"):
            changes += rc.code_changes(baseline.get("commit"), baseline.get("worktree") or {})
    if not changes:
        return None
    data: Dict[str, Any] = {"files": len(changes), "inferred_command": infer_command(rc, feature_dir, changes)}
    data.update(flags(rc, feature_dir, chain, changes))
    who = authors(rc, changes)
    if who:
        data["authors"] = who
    return rec.record(feature, {"kind": "artefact.changed", "actor": rc.actor_script(), "source": "reconcile",
                                "artefacts": now_hashes, "changed": changes, "worktree": rc.worktree_code() or None,
                                "data": data})


def save_baseline(rc: Any) -> None:
    if not rc.has_git:
        return
    rc.invalidate()
    rc.save_state("baseline.json", {"commit": rc.head, "worktree": rc.worktree_code(), "at": now_iso()})
