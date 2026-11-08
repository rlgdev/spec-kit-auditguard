"""Loop feedback: the items archiGuard feeds back from implementation to the domain master design.

archiGuard writes `specs/<feature>/gates/loop-feedback.json`; the archiGuard collector records each item as
`feedback.raised` / `feedback.updated` / `feedback.resolved`; a person triages an item with
`auditguard decide feedback:<id> accept | reject | defer`. This module derives the triage state of every item from
the chains - the chains are the record, nothing else is stored.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from .common import PROJECT_KEY, sha256_json, time_key

FILE_NAME = "loop-feedback.json"
EVENT_KINDS = ("feedback.raised", "feedback.updated", "feedback.resolved")
VERDICTS = ("accept", "reject", "defer")
STATUSES = ("new", "deferred", "accepted", "in-backlog", "rejected", "resolved")
OPEN_STATUSES = ("new", "deferred")
IMPACT_ORDER = {"high": 0, "medium": 1, "low": 2}
KIND_ORDER = ("unmapped-code", "cross-context", "new-unit", "topology", "vocabulary", "domain-change", "map-gap")   # as archiGuard
# the item fields an event carries (besides id, fingerprint and the file context)
ITEM_FIELDS = ("kind", "subject", "title", "observation", "suggestion", "impact", "for", "target", "waiver")
COMPARED_FIELDS = ITEM_FIELDS + ("occurrences",)


def item_fingerprint(item: Dict[str, Any]) -> str:
    """Identity of an item's content: the canonical JSON of the item without `first_seen`."""
    return sha256_json({k: v for k, v in item.items() if k != "first_seen"})


def item_data(item: Dict[str, Any]) -> Dict[str, Any]:
    """The fields of an item a feedback event records."""
    out: Dict[str, Any] = {"id": item.get("id")}
    for key in ITEM_FIELDS:
        out[key] = item.get(key)
    occ = item.get("occurrences")
    out["occurrences"] = int(occ.get("count") or 0) if isinstance(occ, dict) else (int(occ) if isinstance(occ, int) else 0)
    out["first_seen"] = item.get("first_seen")
    return out


def changed_fields(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    return [k for k in COMPARED_FIELDS if before.get(k) != after.get(k)]


def open_in_chain(chain: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """id -> the latest feedback.raised / feedback.updated event of the items that are open in this chain."""
    latest: Dict[str, Dict[str, Any]] = {}
    for ev in chain:
        if ev.get("kind") in EVENT_KINDS:
            fid = (ev.get("data") or {}).get("id")
            if fid:
                latest[str(fid)] = ev
    return {fid: ev for fid, ev in latest.items() if ev.get("kind") != "feedback.resolved"}


def is_feedback_decision(ev: Dict[str, Any]) -> bool:
    return ev.get("kind") == "decision" and str((ev.get("data") or {}).get("subject") or "").startswith("feedback:")


def decision_id(ev: Dict[str, Any]) -> str:
    return str((ev.get("data") or {}).get("subject") or "").split(":", 1)[1]


def _order(ev: Dict[str, Any]) -> Tuple[Any, ...]:
    return (time_key(ev.get("at")), ev.get("seq") or 0)


def latest_decisions(chains: Dict[str, List[Dict[str, Any]]], keys: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, Any]]:
    """id -> the latest `decision` with subject feedback:<id> in the given chains (default: all)."""
    out: Dict[str, Dict[str, Any]] = {}
    for key in (list(keys) if keys is not None else list(chains)):
        for ev in chains.get(key) or []:
            if not is_feedback_decision(ev):
                continue
            fid = decision_id(ev)
            if fid not in out or _order(ev) >= _order(out[fid]):
                out[fid] = ev
    return out


def known_of(decision: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not decision:
        return None
    d = decision.get("data") or {}
    return {"verdict": d.get("verdict"), "ref": d.get("ref"), "at": decision.get("at"), "sprint": decision.get("sprint")}


def status_of(decision: Optional[Dict[str, Any]], open_somewhere: bool) -> str:
    """The triage status (FR-502): the latest decision, unless no feature raises the item any more."""
    verdict = ((decision or {}).get("data") or {}).get("verdict")
    if verdict == "accept":
        return "in-backlog" if ((decision or {}).get("data") or {}).get("ref") else "accepted"
    if not open_somewhere:
        return "resolved"
    if verdict == "reject":
        return "rejected"
    if verdict == "defer":
        return "deferred"
    return "new"


def items(chains: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """Every loop-feedback item of the trail, merged by id across the features, with its triage status.

    A record: the item fields of its latest event, `features` (raised there), `open_in` (still open there),
    `sprints` (every sprint folder with a feedback event or decision of the item), `first_sprint`, `first_at`,
    `last_event`, `home_contexts`, `decisions` (oldest first), `decision` (the latest), `status`, `ref`.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for key, chain in chains.items():
        if key == PROJECT_KEY:
            continue
        open_now = open_in_chain(chain)
        for ev in chain:
            if ev.get("kind") not in EVENT_KINDS:
                continue
            d = ev.get("data") or {}
            fid = d.get("id")
            if not fid:
                continue
            rec = out.setdefault(str(fid), {"id": str(fid), "features": [], "open_in": [], "sprints": [],
                                            "home_contexts": [], "first_at": ev.get("at"), "first_sprint": ev.get("sprint"),
                                            "decisions": []})
            feature = ev.get("feature") or key
            if feature not in rec["features"]:
                rec["features"].append(feature)
            sprint = ev.get("_sprint_dir") or ev.get("sprint")
            if sprint and sprint not in rec["sprints"]:
                rec["sprints"].append(sprint)
            if time_key(ev.get("at")) < time_key(rec.get("first_at")):
                rec["first_at"], rec["first_sprint"] = ev.get("at"), ev.get("sprint")
            if ev.get("kind") != "feedback.resolved":
                for k in ITEM_FIELDS + ("occurrences", "first_seen", "map_version"):
                    if k in d:
                        rec[k] = d[k]
                home = d.get("home_context")
                if home and home not in rec["home_contexts"]:
                    rec["home_contexts"].append(home)
                if "last_event" not in rec or _order(ev) >= _order(rec["last_event"]):
                    rec["last_event"] = ev
            elif "kind" not in rec:
                rec["kind"], rec["subject"] = d.get("kind"), d.get("subject")
            if fid in open_now and feature not in rec["open_in"]:
                rec["open_in"].append(feature)
    for key, chain in chains.items():
        for ev in chain:
            if not is_feedback_decision(ev):
                continue
            rec = out.get(decision_id(ev))
            if rec is None:
                continue
            rec["decisions"].append(ev)
            sprint = ev.get("_sprint_dir") or ev.get("sprint")
            if sprint and sprint not in rec["sprints"]:
                rec["sprints"].append(sprint)
    for rec in out.values():
        rec["decisions"].sort(key=_order)
        rec["decision"] = rec["decisions"][-1] if rec["decisions"] else None
        rec["status"] = status_of(rec["decision"], bool(rec["open_in"]))
        rec["ref"] = ((rec["decision"] or {}).get("data") or {}).get("ref")
    return out


def sort_key(rec: Dict[str, Any]) -> Tuple[Any, ...]:
    kind = rec.get("kind")
    return (IMPACT_ORDER.get(str(rec.get("impact")), 3), KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER),
            str(rec.get("subject") or ""), rec["id"])


def in_sprint(rec: Dict[str, Any], sprint: Optional[str]) -> bool:
    return sprint is None or sprint in (rec.get("sprints") or [])


def counts(records: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """{new, deferred, accepted, in_backlog, rejected, resolved} - the seal's `feedback` field."""
    out = {s.replace("-", "_"): 0 for s in STATUSES}
    for rec in records:
        out[str(rec.get("status")).replace("-", "_")] = out.get(str(rec.get("status")).replace("-", "_"), 0) + 1
    return out


def for_roles(rec: Dict[str, Any]) -> List[str]:
    roles = []
    for f in rec.get("for") or []:
        if isinstance(f, dict):
            label = f.get("role") or f.get("name") or ""
            if f.get("name") and f.get("role") and f["name"] != f["role"]:
                label = f"{f['role']} ({f['name']})"
        else:
            label = str(f)
        if label and label not in roles:
            roles.append(label)
    return roles


def subject_contexts(kind: Optional[str], subject: Optional[str]) -> List[str]:
    """The contexts a subject names (for the backlog labels)."""
    s = str(subject or "")
    out: List[str] = []
    if kind == "cross-context":
        out = s.split("->")
    elif kind in ("vocabulary", "topology"):
        head = s.split(":", 1)[0]
        out = [] if head == "repo" else [head]
    elif kind == "map-gap":
        head = s.split(":", 1)[0]
        out = [] if head in ("map", "deployable") else [head]
    elif kind == "domain-change":
        parts = s.split(":")
        if parts[0] == "relation" and len(parts) > 1:
            out = parts[1].split("->")
        elif parts[0] == "published" and len(parts) > 1:
            out = [parts[1]]
        elif parts[0] == "rule" and len(parts) > 2:
            out = [parts[2]]
    return [c.strip() for c in out if c.strip()]
