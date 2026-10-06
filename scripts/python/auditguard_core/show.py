"""`auditguard show`: the trail in the terminal."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional

from .common import PROJECT_KEY, resolve_feature_dir
from .model import Model, actor_label, describe, group, hhmm, is_flagged
from .render_md import load_verification
from .store import Store, clean


def _fit(text: str, width: int) -> str:
    text = str(text).replace("\n", " ")
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def show(root: Path, cfg: Any, *, feature_arg: Optional[str], sprint: Optional[str], stage: Optional[str],
         kind: Optional[str], actor: Optional[str], open_only: bool, as_json: bool, all_features: bool) -> str:
    store = Store(root, cfg)
    model = Model(store, cfg, load_verification(cfg))
    key: Optional[str] = None
    if not all_features:
        fd = resolve_feature_dir(root, feature_arg, required=False)
        key = fd.name if fd is not None else None
    events = model.events(sprint, key)
    if stage:
        events = [e for e in events if e.get("stage") == stage]
    if kind:
        events = [e for e in events if str(e.get("kind")).startswith(kind) or group(str(e.get("kind"))) == kind]
    if actor:
        events = [e for e in events if (e.get("actor") or {}).get("type") == actor or (e.get("actor") or {}).get("id") == actor]
    items = model.open_items(key)
    if as_json:
        return json.dumps({"feature": key, "sprint": sprint, "events": [clean(e) for e in events],
                           "open_items": items}, indent=2, ensure_ascii=False)
    width = shutil.get_terminal_size((140, 40)).columns
    lines = []
    title = key or "all features"
    lines.append(f"auditGuard | {title}" + (f" | sprint {sprint}" if sprint else ""))
    if key and key != PROJECT_KEY:
        lines.append(f"stages: {model.stage_line(key)}")
    if not open_only:
        lines.append("")
        detail_w = max(30, width - 72)
        for e in events:
            t, d = describe(e)
            flag = "!" if is_flagged(e) else " "
            lab = {"verified": "✔", "ephemeral": "◌", "unanchored": "·", "mismatch": "✖"}.get(model.label(e) or "", " ")
            feat = "" if key else f"{(Path(str(e.get('feature'))).name if e.get('feature') else PROJECT_KEY)[:16]:<16} "
            lines.append(f"{flag}{lab} {e.get('seq', ''):>4} {hhmm(e.get('at'))} {feat}{_fit(actor_label(e.get('actor'), e.get('data')), 22):<22} "
                         f"{_fit(t, 34):<34} {_fit(d, detail_w)}")
        if not events:
            lines.append("  (no events)")
    lines.append("")
    lines.append("open items:")
    any_item = False
    for label, values in items.items():
        for v in values:
            lines.append(f"  [{label}] {v}")
            any_item = True
    if not any_item:
        lines.append("  none")
    return "\n".join(lines)
