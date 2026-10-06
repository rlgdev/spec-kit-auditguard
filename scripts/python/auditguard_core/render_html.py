"""The HTML viewer: audit/viewer/index.html (static, unchanged between renders) + audit/viewer/data.js.

data.js sets `window.AUDITGUARD = {...}` and is loaded with a <script src> tag, so the viewer works from
file:// with no server and no network. Summaries are computed here, so the viewer stays small."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .common import PROJECT_KEY, UNASSIGNED, read_bytes, read_text, rel_path, time_key, write_text
from .model import Model, actor_label, describe, group, is_flagged
from .recorder import MILESTONES
from .render_md import load_verification

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "viewer" / "index.html"


def viewer_html() -> bytes:
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"viewer template missing: {TEMPLATE}")
    return read_bytes(TEMPLATE)


def _evidence(model: Model, ev: Dict[str, Any], inline_limit: int, bucket: Dict[str, Any], prefix: str) -> List[Dict[str, Any]]:
    out = []
    for ref in ev.get("evidence") or []:
        item = {"name": ref.get("name"), "sha256": ref.get("sha256"), "size": ref.get("size"),
                "source_path": ref.get("source_path"), "snapshot": ref.get("snapshot")}
        path = model.store.evidence_path(ev, ref)
        if path is not None and path.is_file():
            rel = rel_path(path, model.store.audit)
            item["href"] = f"{prefix}{rel}"
            sha = str(ref.get("sha256"))
            if sha not in bucket:
                data = path.read_bytes()
                text = None
                if len(data) <= inline_limit and b"\x00" not in data[:4096]:
                    text = data.decode("utf-8", errors="replace")
                bucket[sha] = {"name": ref.get("name"), "href": item["href"], "size": len(data), "text": text}
        out.append(item)
    return out


def build_data(store: Any, cfg: Any, *, sprint: Optional[str] = None, prefix: str = "../") -> Dict[str, Any]:
    verification = load_verification(cfg)
    model = Model(store, cfg, verification)
    inline_limit = int(cfg.get("viewer", "inline_kb") or 64) * 1024
    evidence_bucket: Dict[str, Any] = {}
    events = []
    newest = 0.0
    for key in model.keys():
        for ev in model.chains[key]:
            if sprint is not None and ev.get("_sprint_dir") != sprint:
                continue
            title, detail = describe(ev)
            newest = max(newest, time_key(ev.get("recorded") or ev.get("at")))
            clean = {k: v for k, v in ev.items() if not k.startswith("_")}
            clean["evidence"] = _evidence(model, ev, inline_limit, evidence_bucket, prefix)
            clean["_key"] = key
            clean["_dir"] = ev.get("_sprint_dir")
            clean["_title"] = title
            clean["_detail"] = detail
            clean["_group"] = group(str(ev.get("kind")))
            clean["_actor"] = actor_label(ev.get("actor"), ev.get("data"))
            clean["_flag"] = is_flagged(ev)
            clean["_milestone"] = ev.get("kind") in MILESTONES
            lab = model.label(ev)
            if lab:
                clean["_label"] = lab
                det = ((verification or {}).get("golden") or {}).get("events", {}).get(ev.get("hash")) or {}
                if det.get("files"):
                    clean["_files"] = det["files"]
                if det.get("checks"):
                    clean["_checks"] = det["checks"]
            events.append(clean)
    sprints = []
    for s in model.sprints():
        if sprint is not None and s != sprint:
            continue
        entry = dict(model.sprint_entry(s))
        seal = model.seal(s)
        entry["seal"] = {"hash": seal.get("hash"), "at": seal.get("at"), "closed_by": seal.get("closed_by"),
                         "warnings": seal.get("warnings")} if seal else None
        entry["has_folder"] = (store.sprints_dir / s).is_dir()
        sprints.append(entry)
    features = []
    for key in model.keys():
        st = model.status(key)
        per_sprint = {}
        for s in model.sprints():
            if sprint is not None and s != sprint:
                continue
            cell = model.cell(key, s)
            if cell.get("events"):
                per_sprint[s] = cell
        if not per_sprint and sprint is not None:
            continue
        features.append({"key": key, "path": f"specs/{key}" if key != PROJECT_KEY else None,
                         "stages": st.to_dict() if key != PROJECT_KEY else None, "cells": per_sprint,
                         "chain": model.chain_summary(key)})
    waivers = []
    for key, rec in model.waivers_in_force():
        waivers.append({"key": key, **rec})
    v_stale = None
    if verification:
        v_stale = time_key(verification.get("generated")) < newest
    return {
        "v": 1,
        "tool": f"auditGuard {__version__}",
        "project": Path(store.root).name,
        "scope": {"sprint": sprint},
        "generated_from": {"events": len(events), "newest_event": newest},
        "stages": cfg.stage_names(),
        "register": sprints,
        "features": features,
        "events": events,
        "waivers_in_force": waivers,
        "open_items": model.open_items(),
        "verification": verification,
        "verification_stale": v_stale,
        "evidence": evidence_bucket,
        "theme": cfg.get("viewer", "theme") or "auto",
        "unassigned": UNASSIGNED,
        "project_key": PROJECT_KEY,
    }


def data_js(data: Dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    return "/* generated by auditGuard - the journals are the record; this is a view */\nwindow.AUDITGUARD = " + payload + ";\n"


def render_viewer(store: Any, cfg: Any) -> List[Path]:
    out_dir = cfg.audit_root / "viewer"
    written = []
    html = viewer_html()
    index = out_dir / "index.html"
    if not index.is_file() or index.read_bytes() != html:
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_bytes(html)
        written.append(index)
    text = data_js(build_data(store, cfg))
    target = out_dir / "data.js"
    if not target.is_file() or read_text(target) != text:
        write_text(target, text)
        written.append(target)
    return written


def pack_viewer(store: Any, cfg: Any, sprint: str) -> Dict[str, bytes]:
    data = build_data(store, cfg, sprint=sprint, prefix="../")
    return {"index.html": viewer_html(), "data.js": data_js(data).encode("utf-8")}
