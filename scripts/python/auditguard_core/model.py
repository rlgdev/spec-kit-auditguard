"""The read model shared by the Markdown views, the terminal view and the HTML viewer."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .collectors.waivers import current_waivers
from .common import PROJECT_KEY, UNASSIGNED, parse_date, read_json, short, time_key, today
from .decisions import open_escalations
from .check import open_commands
from .recorder import MILESTONES, StageStatus, stage_status
from .store import Store

KIND_GROUP = {
    "command": ("command.started", "command.finished", "command.abandoned"),
    "stage": ("stage.entered", "stage.reentered", "stage.completed"),
    "gate": ("gate.verdict", "report.saved"),
    "waiver": ("waiver.added", "waiver.changed", "waiver.removed", "waiver.approved", "waiver.revoked",
               "waiver.superseded", "waiver.expired"),
    "decision": ("decision",) + MILESTONES,
    "escalation": ("escalation.raised", "escalation.decided", "escalation.withdrawn"),
    "change": ("artefact.changed", "commit.merged"),
    "session": ("session.started", "session.ended"),
    "sprint": ("sprint.opened", "sprint.closed"),
    "note": ("note",),
    "system": ("collector.error", "collector.ran", "ci.verified"),
}
GROUP_OF = {k: g for g, kinds in KIND_GROUP.items() for k in kinds}


def group(kind: str) -> str:
    if kind in GROUP_OF:
        return GROUP_OF[kind]
    return kind.split(".")[0] if "." in kind else "other"


def hhmm(at: Optional[str]) -> str:
    if not at:
        return "-"
    return f"{at[5:10]} {at[11:16]}"


def actor_label(actor: Optional[Dict[str, Any]], data: Optional[Dict[str, Any]] = None) -> str:
    a = actor or {}
    if a.get("type") == "script" and (data or {}).get("by"):
        return f"{a.get('id')} · by {data['by']}"
    label = f"{a.get('type', '?')} {a.get('id', '?')}"
    if a.get("role"):
        label += f" ({a['role']})"
    return label


def _cmd(ev: Dict[str, Any]) -> str:
    return str(ev.get("command") or "").replace("speckit.", "")


def gate_text(g: Dict[str, Any]) -> str:
    if g.get("tool") == "scopeguard":
        it = f" ({g['iterations']} it.)" if g.get("iterations") else ""
        cov = f" {g['coverage']}" if g.get("coverage") else ""
        return f"scopeGuard {g.get('gate')}{cov} {str(g.get('status') or '').upper()}{it}"
    it = f" ({g['iterations']} it.)" if g.get("iterations") not in (None, 0) else ""
    adv = f", {g['advisory']} advisory" if g.get("advisory") else ""
    blk = f", {g['blocking']} blocking" if g.get("blocking") else ""
    return f"archiGuard {g.get('step')} {str(g.get('status') or '').upper()}{blk}{adv}{it}"


def describe(ev: Dict[str, Any]) -> Tuple[str, str]:
    """(title, detail) of an event for the views."""
    kind = ev.get("kind") or ""
    d = ev.get("data") or {}
    if kind == "command.started":
        return f"{_cmd(ev)} started", (f"{len(ev.get('artefacts') or {})} artefact(s) hashed" if ev.get("artefacts") else "")
    if kind in ("command.finished", "command.abandoned"):
        verb = "finished" if kind == "command.finished" else "abandoned"
        changed = ev.get("changed") or []
        names = ", ".join(Path(str(c.get("path"))).name for c in changed[:4]) + (f" +{len(changed) - 4}" if len(changed) > 4 else "")
        parts = [names] if changed else ["no artefact changed"]
        parts += [gate_text(g) for g in d.get("gates") or []]
        if d.get("waiver_changes"):
            parts.append(f"{d['waiver_changes']} waiver change(s)")
        if d.get("started_missing"):
            parts.append("before_ hook missed")
        if d.get("outcome_file"):
            parts.append(f"see {d['outcome_file']}")
        return f"{_cmd(ev)} {verb} ({ev.get('outcome')})", " · ".join(p for p in parts if p)
    if kind == "stage.entered":
        return f"{d.get('stage')} stage entered", f"by /speckit.{str(d.get('by_command', '')).replace('speckit.', '')}" if d.get("by_command") else (f"after {d.get('after_stage')}" if d.get("after_stage") else "")
    if kind == "stage.reentered":
        return f"↺ {d.get('stage')} stage re-entered", f"completed {hhmm(d.get('completed_at'))} before" if d.get("completed_at") else ""
    if kind == "stage.completed":
        return f"✔ {d.get('stage')} stage completed", f"by {d.get('by_kind')}" + (f" ({d['by']})" if d.get("by") else "")
    if kind == "gate.verdict":
        text = gate_text(d)
        extra = []
        if d.get("open_items"):
            extra.append("open: " + ", ".join(d["open_items"][:6]))
        if d.get("escalation"):
            extra.append(f"escalated: {d['escalation']}")
        return "gate verdict", " · ".join([text] + extra)
    if kind == "report.saved":
        return "report saved", f"{d.get('tool')}: {d.get('name')}"
    if kind.startswith("waiver."):
        what = d.get("item") or ", ".join(d.get("rules") or []) or d.get("rule") or ""
        verb = kind.split(".", 1)[1]
        bits = [f"{d.get('id')} {verb}"]
        if what and what not in str(d.get("id")):
            bits.append(f"({what})")
        if d.get("reason"):
            bits.append(f"— \"{d['reason']}\"")
        if d.get("changed_fields"):
            bits.append("changed: " + ", ".join(f"{f} {(d.get('before') or {}).get(f)!r} → {(d.get('after') or {}).get(f)!r}"
                                                for f in d["changed_fields"][:3]))
        if d.get("approver"):
            bits.append(f"approver {d['approver']}")
        if d.get("expires"):
            bits.append(f"expires {d['expires']}")
        return f"waiver {verb}", " ".join(bits)
    if kind == "decision":
        who = d.get("by") or ""
        role = f" ({d['role']})" if d.get("role") else ""
        reason = f" — \"{d['reason']}\"" if d.get("reason") else ""
        return f"decision: {d.get('subject')} → {d.get('verdict')}", f"{who}{role}{reason}"
    if kind in MILESTONES:
        who = d.get("by") or (ev.get("actor") or {}).get("id") or ""
        extra = f" · {len(d.get('hashes') or {})} artefact(s) hashed" if d.get("hashes") else ""
        reason = f" — \"{d['reason']}\"" if d.get("reason") else ""
        return f"★ {kind}", f"{who}{reason}{extra}"
    if kind == "escalation.raised":
        items = ", ".join((d.get("items") or [])[:6])
        return "escalation raised", f"{d.get('tool')} {d.get('gate')}: {Path(str(d.get('note'))).name}" + (f" · {items}" if items else "")
    if kind == "escalation.decided":
        return f"escalation decided: {d.get('verdict')}", f"{Path(str(d.get('note'))).name} · {d.get('by')}" + (f" — \"{d['reason']}\"" if d.get("reason") else "")
    if kind == "escalation.withdrawn":
        return "escalation withdrawn", f"{Path(str(d.get('note'))).name} (the gate passed later)"
    if kind == "artefact.changed":
        changed = ev.get("changed") or []
        names = ", ".join(Path(str(c.get("path"))).name for c in changed[:4]) + (f" +{len(changed) - 4}" if len(changed) > 4 else "")
        flags = []
        if d.get("after_signoff"):
            flags.append("after sign-off")
        if d.get("after_handover"):
            flags.append("after handover")
        if d.get("inferred_command"):
            flags.append(f"inferred: {str(d['inferred_command']).replace('speckit.', '')}")
        who = sorted(set((d.get("authors") or {}).values()))
        if who:
            flags.append("by " + ", ".join(who[:2]))
        return "⚠ changed outside a recorded command", " · ".join([names] + flags)
    if kind == "commit.merged":
        return "merged", f"{str(d.get('commit'))[:10]} {d.get('subject') or ''}" + (f" (PR #{d['pr']})" if d.get("pr") else "")
    if kind == "session.started":
        return "session started", f"{d.get('agent')} {short(d.get('session'), 8)}"
    if kind == "session.ended":
        return "session ended", f"{d.get('agent')} {short(d.get('session'), 8)}" + (f" ({d['reason']})" if d.get("reason") else "")
    if kind == "sprint.opened":
        return f"sprint {d.get('id')} opened", f"{d.get('name') or ''} {d.get('start') or ''}..{d.get('end') or ''}".strip()
    if kind == "sprint.closed":
        return f"sprint {d.get('id')} closed", f"{len(d.get('warnings') or [])} warning(s)"
    if kind == "note":
        return "note", str(d.get("text"))
    if kind == "collector.error":
        return "collector error", f"{d.get('name')}: {d.get('message')}"
    if kind == "ci.verified":
        return "verified in CI", f"internal {d.get('internal')} · golden {d.get('golden')}"
    return kind, ""


def is_flagged(ev: Dict[str, Any]) -> bool:
    return ev.get("kind") in ("artefact.changed", "stage.reentered", "collector.error") or (
        ev.get("kind") == "command.abandoned" and ev.get("outcome") in ("escalated", "error"))


class Model:
    def __init__(self, store: Store, cfg: Any, verification: Optional[Dict[str, Any]] = None):
        self.store = store
        self.cfg = cfg
        self.register = store.register
        self.chains: Dict[str, List[Dict[str, Any]]] = {k: store.load_chain(k) for k in store.chain_keys()}
        self.verification = verification

    def keys(self) -> List[str]:
        return sorted(self.chains, key=lambda k: (k == PROJECT_KEY, k))

    def feature_keys(self) -> List[str]:
        return [k for k in self.keys() if k != PROJECT_KEY]

    def sprints(self) -> List[str]:
        ids = self.register.ids()
        for s in self.store.sprint_dirs():
            if s not in ids:
                ids.append(s)
        return sorted(ids, key=self.register.order_key)

    def events(self, sprint: Optional[str] = None, key: Optional[str] = None) -> List[Dict[str, Any]]:
        keys = [key] if key else self.keys()
        out = []
        for k in keys:
            out += [e for e in self.chains.get(k, []) if sprint is None or e.get("_sprint_dir") == sprint]
        if key:
            return sorted(out, key=lambda e: e.get("seq") or 0)
        return sorted(out, key=lambda e: (time_key(e.get("at")), e.get("feature") or "", e.get("seq") or 0))

    def status(self, key: str, upto_sprint: Optional[str] = None) -> StageStatus:
        chain = self.chains.get(key, [])
        if upto_sprint is not None:
            limit = self.register.order_key(upto_sprint)
            chain = [e for e in chain if self.register.order_key(e.get("_sprint_dir") or "") <= limit]
        return stage_status(self.cfg, chain)

    def stage_line(self, key: str, upto_sprint: Optional[str] = None, *, glyphs: bool = True) -> str:
        st = self.status(key, upto_sprint)
        parts = []
        for name in st.names:
            state = st.state[name]
            if state == "completed":
                done = st.completed[name] or {}
                parts.append(f"{name} ✔ {str(done.get('at') or '')[:10]}" + (f" by {done['by']}" if done.get("by") else ""))
            elif state in ("entered", "reentered"):
                parts.append(f"{name} ● in progress" + (" (re-entered)" if state == "reentered" else ""))
            else:
                parts.append(f"{name} ○")
        return " · ".join(parts)

    def cell(self, key: str, sprint: str) -> Dict[str, Any]:
        """The overview cell: what happened to a feature in one sprint."""
        evs = [e for e in self.chains.get(key, []) if e.get("_sprint_dir") == sprint]
        if not evs:
            return {"events": 0}
        st_before = self.status(key, None)
        st = self.status(key, sprint)
        stages = []
        for name in st.names:
            in_sprint = [e for e in evs if (e.get("data") or {}).get("stage") == name and str(e.get("kind")).startswith("stage.")]
            touched = in_sprint or any(e.get("stage") == name for e in evs)
            if not touched:
                continue
            stages.append({"stage": name, "state": st.state[name]})
        flagged = sum(1 for e in evs if is_flagged(e))
        del st_before
        return {"events": len(evs), "stages": stages, "flagged": flagged}

    def decisions(self, key: Optional[str] = None, sprint: Optional[str] = None) -> List[Dict[str, Any]]:
        return [e for e in self.events(sprint, key) if e.get("kind") == "decision" or e.get("kind") in MILESTONES]

    def gate_verdicts(self, key: Optional[str] = None, sprint: Optional[str] = None) -> List[Dict[str, Any]]:
        return [e for e in self.events(sprint, key) if e.get("kind") == "gate.verdict"]

    def waiver_events(self, key: Optional[str] = None, sprint: Optional[str] = None) -> List[Dict[str, Any]]:
        return [e for e in self.events(sprint, key) if str(e.get("kind")).startswith("waiver.")]

    def oob(self, key: Optional[str] = None, sprint: Optional[str] = None) -> List[Dict[str, Any]]:
        return [e for e in self.events(sprint, key) if e.get("kind") == "artefact.changed"]

    def waivers_in_force(self, key: Optional[str] = None) -> List[Tuple[str, Dict[str, Any]]]:
        out = []
        for k in ([key] if key else self.keys()):
            for wid, rec in sorted(current_waivers(self.chains.get(k, [])).items()):
                if rec.get("status") in ("revoked", "superseded"):
                    continue
                out.append((k, rec))
        return out

    def open_items(self, key: Optional[str] = None) -> Dict[str, List[str]]:
        keys = [key] if key else self.keys()
        days = int(self.cfg.get("render", "expiring_days") or 30)
        items: Dict[str, List[str]] = {"escalations": [], "decisions": [], "expiring": [], "commands": [], "stages": []}
        for k in keys:
            chain = self.chains.get(k, [])
            for e in open_escalations(chain):
                items["escalations"].append(f"{k}: {Path(str((e.get('data') or {}).get('note'))).name}")
            pending_spec = [e for e in chain if e.get("kind") == "artefact.changed" and (e.get("data") or {}).get("after_handover")]
            decided = {(e.get("data") or {}).get("change_event") for e in chain if e.get("kind") == "decision"
                       and (e.get("data") or {}).get("subject") == "spec-change"}
            for e in pending_spec:
                if e.get("hash") not in decided:
                    items["decisions"].append(f"{k}: spec change #{e.get('seq')} awaits a decision (decide spec-change)")
            for e in open_commands(chain):
                items["commands"].append(f"{k}: {_cmd(e)} #{e.get('seq')}")
            if k != PROJECT_KEY:
                st = stage_status(self.cfg, chain)
                for name in st.names:
                    if st.state[name] in ("entered", "reentered"):
                        items["stages"].append(f"{k}: {name} in progress")
            for wid, rec in sorted(current_waivers(chain).items()):
                exp = parse_date(rec.get("expires"))
                if exp and rec.get("status") not in ("revoked", "superseded") and (exp - today()).days <= days:
                    items["expiring"].append(f"{k}: {wid} expires {exp.isoformat()}")
        return items

    def label(self, ev: Dict[str, Any]) -> Optional[str]:
        if not self.verification:
            return None
        golden = self.verification.get("golden") or {}
        return ((golden.get("events") or {}).get(ev.get("hash")) or {}).get("label")

    def chain_summary(self, key: str) -> Dict[str, Any]:
        chain = self.chains.get(key, [])
        return {"count": len(chain), "head": chain[-1].get("hash") if chain else None,
                "first": chain[0].get("hash") if chain else None}

    def seal(self, sprint: str) -> Optional[Dict[str, Any]]:
        return read_json(self.store.sprints_dir / sprint / "seal.json", None)

    def sprint_entry(self, sprint: str) -> Dict[str, Any]:
        if sprint == UNASSIGNED:
            return {"id": UNASSIGNED, "name": "events outside any sprint", "status": "unassigned"}
        return self.register.get(sprint) or {"id": sprint, "status": "unknown"}
