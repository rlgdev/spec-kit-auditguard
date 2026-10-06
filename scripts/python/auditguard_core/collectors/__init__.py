"""Collectors: what scopeGuard, archiGuard, the decision ledger, Spec Kit workflow runs, git and plug-ins left
behind, turned into events. Run at every hook and by `auditguard collect`."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common import PROJECT_KEY, AuditGuardError
from ..context import key_of
from . import archiguard, gitmerge, plugged, scopeguard, waivers, workflow
from .base import Emit


def run_collectors(rec: Any, feature_dir: Optional[Path]) -> List[Dict[str, Any]]:
    """Run every enabled collector for the feature (None = project level) and append what they emit."""
    rc = rec.rc
    cfg = rc.cfg
    feature = rec.feature_path(feature_dir)
    appended: List[Dict[str, Any]] = []

    def flush(emits: List[Emit]) -> None:
        for e in emits:
            appended.append(rec.record(e.feature, e.spec, e.evidence))

    def chain_of(f: Optional[str]) -> List[Dict[str, Any]]:
        return rec.chain(key_of(f))

    errors: List[str] = []

    def safe(label: str, fn: Any) -> None:
        try:
            flush(fn())
        except AuditGuardError as exc:
            errors.append(f"{label}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a collector must never stop the recording
            errors.append(f"{label}: {type(exc).__name__}: {exc}")

    if feature_dir is not None and feature_dir.is_dir():
        if cfg.collector_enabled("archiguard"):
            safe("archiguard", lambda: archiguard.collect(rc, feature_dir, feature, chain_of(feature)))
        if cfg.collector_enabled("scopeguard"):
            safe("scopeguard", lambda: scopeguard.collect(rc, feature_dir, feature, chain_of(feature)))
    safe("waivers", lambda: waivers.collect(rc, feature_dir if feature_dir is not None and feature_dir.is_dir() else None,
                                            feature, chain_of(feature), chain_of(None)))
    if cfg.collector_enabled("workflow"):
        chains: Dict[Optional[str], List[Dict[str, Any]]] = {}
        safe("workflow", lambda: workflow.collect(rc, chains, chain_of))
    if feature is not None and cfg.get("collectors", "git", "enabled", default=True):
        safe("git", lambda: gitmerge.collect(rc, feature, chain_of(feature)))
    for name, conf in sorted(cfg.plugin_collectors().items()):
        safe(name, lambda n=name, c=conf: plugged.collect(rc, n, c, feature_dir, feature, chain_of(feature)))
    for err in errors:
        try:
            appended.append(rec.record(feature, {"kind": "collector.error", "actor": rc.actor_script(),
                                                 "source": "collector", "data": {"name": err.split(":", 1)[0],
                                                                                 "message": err.split(":", 1)[-1].strip()}}))
        except AuditGuardError:
            pass
    return appended


__all__ = ["run_collectors", "PROJECT_KEY"]
