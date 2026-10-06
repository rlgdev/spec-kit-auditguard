"""Command line: auditguard <command> [options]. `auditguard <command> --help` lists the options."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, List, Optional

from . import __version__
from .common import (EXIT_ERROR, EXIT_OK, EXIT_PROBLEMS, HOOK_EVENTS, PROJECT_KEY, RUNTIME_EVENTS, STATE_REL,
                     AuditGuardError, find_project_root, is_ci, now_iso, rel_path, resolve_feature_dir, write_json)
from .config import load_config

EPILOG = "Exit codes: 0 ok · 1 verify/check found problems · 2 cannot run · 3 a command for people ran in an agent context"


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", help="project root (default: the nearest directory with .specify/)")
    p.add_argument("--config", help="config file (default: .specify/extensions/auditguard/auditguard-config.yml)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--verbose", action="store_true", help="show tracebacks on errors")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auditguard", description=f"auditGuard {__version__} - the audit trail of the Spec Kit SDLC",
                                     epilog=EPILOG)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("hook", help="record a Spec Kit hook (before_<cmd> / after_<cmd>) or runtime event")
    p.add_argument("event", help=f"one of: before_/after_ + {', '.join(sorted({e.split('_', 1)[1] for e in HOOK_EVENTS}))}; or {', '.join(RUNTIME_EVENTS)}")
    p.add_argument("--feature-dir")
    p.add_argument("--via", choices=["hooks", "workflow"], help="who calls: the Spec Kit hook or a workflow shell step")
    _common(p)

    p = sub.add_parser("event", help="agent runtime event (Spec Kit events dispatcher): payload on stdin")
    p.add_argument("name", choices=["session_start", "stop", "session_end", "pre_tool_use"])
    _common(p)

    p = sub.add_parser("guard", help="the pre_tool_use guard (payload on stdin)")
    _common(p)

    p = sub.add_parser("collect", help="collect new evidence (gate reports, waivers, decisions) into the trail now")
    p.add_argument("--feature-dir")
    p.add_argument("--all", action="store_true", help="every feature under specs/")
    _common(p)

    p = sub.add_parser("decide", help="record a decision of a person (human only)")
    p.add_argument("subject", help="design | implement | pr | spec-change | escalation:<note or id> | gate:<step-id>")
    p.add_argument("verdict", help="approve | reject | accept | defer")
    p.add_argument("--by", help="the person who decided")
    p.add_argument("--role", help="e.g. 'tech lead', 'lead architect'")
    p.add_argument("--reason")
    p.add_argument("--feature-dir")
    _common(p)

    p = sub.add_parser("note", help="add a note to the trail (human only)")
    p.add_argument("text")
    p.add_argument("--by")
    p.add_argument("--feature-dir")
    _common(p)

    p = sub.add_parser("sprint", help="list | current | open <id> | close [<id>]")
    p.add_argument("action", choices=["list", "current", "open", "close"])
    p.add_argument("id", nargs="?")
    p.add_argument("--name")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--goal")
    p.add_argument("--by")
    p.add_argument("--close-current", action="store_true", help="open: close the open sprint first")
    _common(p)

    p = sub.add_parser("anchor", help="anchor chain heads (git note) and a sealed sprint (annotated tag) in git (human / CI)")
    p.add_argument("--sprint")
    p.add_argument("--push", action="store_true")
    p.add_argument("--by")
    _common(p)

    p = sub.add_parser("render", help="rebuild the Markdown views (and the HTML viewer with --html)")
    p.add_argument("--sprint")
    p.add_argument("--feature-dir")
    p.add_argument("--all", action="store_true")
    p.add_argument("--html", action="store_true")
    _common(p)

    p = sub.add_parser("show", help="the trail in the terminal")
    p.add_argument("--feature-dir")
    p.add_argument("--all", action="store_true", help="every feature")
    p.add_argument("--sprint")
    p.add_argument("--stage")
    p.add_argument("--kind", help="a kind prefix or group: command, gate, waiver, decision, escalation, change, session, note")
    p.add_argument("--actor", help="human | agent | ci | script, or a name")
    p.add_argument("--open", action="store_true", help="only the open items")
    _common(p)

    p = sub.add_parser("verify", help="verify chains, seals and evidence (and the golden sources with --golden)")
    p.add_argument("--sprint")
    p.add_argument("--all", action="store_true")
    p.add_argument("--golden", action="store_true", help="also verify against git and the other golden sources")
    p.add_argument("--recompute", action="store_true", help="G8: recompute scopeGuard reports at their commits")
    p.add_argument("--offline", action="store_true", help="no network (skip the pushed check of the anchor note)")
    p.add_argument("--pack", help="verify an audit pack (zip) without the repository")
    p.add_argument("--record", action="store_true", help="append ci.verified to the project journal")
    p.add_argument("--no-render", action="store_true", help="do not refresh the views with the new labels")
    p.add_argument("--out", help="also write the report as Markdown to this file (e.g. the CI job summary)")
    _common(p)

    p = sub.add_parser("check", help="evaluate the completeness rules")
    p.add_argument("--feature-dir")
    p.add_argument("--sprint")
    _common(p)

    p = sub.add_parser("export", help="audit pack of a sprint (zip, verifies offline)")
    p.add_argument("--sprint", required=True)
    p.add_argument("--out")
    _common(p)

    p = sub.add_parser("serve", help="serve the viewer on localhost and re-render on change")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true")
    _common(p)

    p = sub.add_parser("configure", help="apply the config to Spec Kit's hooks and show what is in force")
    p.add_argument("--dry-run", action="store_true")
    _common(p)

    p = sub.add_parser("version", help="print the version")
    _common(p)
    return parser


def _log_event_error(root: Path, message: str) -> None:
    try:
        path = root / STATE_REL / "events.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{now_iso()} {message}\n")
    except OSError:
        pass


def main(argv: Optional[List[str]] = None) -> None:
    sys.exit(run(argv))


def run(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_ERROR
    if args.command == "version":
        print(f"auditGuard {__version__}")
        return EXIT_OK
    try:
        root = find_project_root(getattr(args, "root", None))
    except AuditGuardError as exc:
        print(f"auditGuard: ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    # agent events must never fail the agent: they log and exit 0 (the guard blocks with exit 2 on purpose)
    if args.command in ("event", "guard"):
        return _event(root, args)
    try:
        cfg = load_config(root, getattr(args, "config", None))
        return _dispatch(root, cfg, args)
    except AuditGuardError as exc:
        if args.command == "hook":
            mode_enforce = os.environ.get("AUDITGUARD_MODE", "") == "enforce"
            try:
                mode_enforce = mode_enforce or load_config(root, getattr(args, "config", None)).enforce
            except AuditGuardError:
                pass
            print(f"auditGuard: could not record ({exc})")
            return EXIT_ERROR if mode_enforce else EXIT_OK
        print(f"auditGuard: ERROR: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001
        if getattr(args, "verbose", False):
            traceback.print_exc()
        if args.command == "hook":
            print(f"auditGuard: could not record ({type(exc).__name__}: {exc})")
            return EXIT_OK
        print(f"auditGuard: ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _event(root: Path, args: argparse.Namespace) -> int:
    from .hooks import Hooks, parse_payload
    text = sys.stdin.read() if not sys.stdin.isatty() else ""
    payload = parse_payload(text)
    name = getattr(args, "name", "pre_tool_use") if args.command == "event" else "pre_tool_use"
    try:
        cfg = load_config(root, getattr(args, "config", None))
    except AuditGuardError as exc:
        _log_event_error(root, f"{name}: config: {exc}")
        return EXIT_OK
    if name == "pre_tool_use":
        from .guard import evaluate
        try:
            code, message = evaluate(payload, root, cfg)
        except Exception as exc:  # noqa: BLE001 - fail open
            _log_event_error(root, f"guard: {type(exc).__name__}: {exc}")
            return EXIT_OK
        if message:
            print(message, file=sys.stderr)
        return code
    try:
        hooks = Hooks(root, cfg)
        hooks.run(name, None, payload)
        if hooks.rec.appended and cfg.get("render", "on_hook", default=True):
            _render(root, cfg, html=bool(cfg.get("render", "html_on_hook")))
    except Exception as exc:  # noqa: BLE001
        _log_event_error(root, f"{name}: {type(exc).__name__}: {exc}")
    return EXIT_OK


def _render(root: Path, cfg: Any, *, html: bool = False, keys: Optional[List[str]] = None,
            sprints: Optional[List[str]] = None) -> List[Path]:
    from .render_md import render_all
    from .store import Store
    store = Store(root, cfg)
    written = render_all(store, cfg, keys=keys, sprints=sprints)
    if html:
        from .render_html import render_viewer
        written += render_viewer(store, cfg)
    return written


def _dispatch(root: Path, cfg: Any, args: argparse.Namespace) -> int:
    cmd = args.command
    if cmd == "hook":
        from .hooks import Hooks
        via = args.via
        if via and via != cfg["integration"]:
            print(f"auditGuard: {args.event} skipped here - integration is '{cfg['integration']}', so "
                  + ("the Spec Kit hooks record." if cfg["integration"] == "hooks" else "the workflow shell steps record.")
                  + " Nothing to do; continue.")
            return EXIT_OK
        line = Hooks(root, cfg).run(args.event, args.feature_dir, {})
        if cfg.get("render", "on_hook", default=True):
            _render(root, cfg, html=bool(cfg.get("render", "html_on_hook")))
        if line:
            print(line)
        return EXIT_OK

    if cmd == "collect":
        from .collectors import run_collectors
        from .common import feature_dirs
        from .context import RunContext
        from .hooks import Hooks
        from .reconcile import reconcile, save_baseline
        from .recorder import Recorder
        hooks = Hooks(root, cfg)
        closed = hooks.close_open(reason="collect", only_stopped=True)
        rc = RunContext(root, cfg)
        rec = Recorder(rc)
        targets = feature_dirs(root) if args.all else [resolve_feature_dir(root, args.feature_dir, required=False)]
        n = len(closed)
        for i, fd in enumerate(targets):
            if reconcile(rec, fd, include_code=(i == 0)):
                n += 1
            n += len(run_collectors(rec, fd))
        if args.all or not targets:
            n += len(run_collectors(rec, None))
        save_baseline(rc)
        if cfg.get("render", "on_hook", default=True):
            _render(root, cfg, html=bool(cfg.get("render", "html_on_hook")))
        print(f"auditGuard {__version__} | collect | {n} event(s) recorded"
              + (f" ({len(closed)} stopped command(s) closed)" if closed else ""))
        return EXIT_OK

    if cmd == "decide":
        from .decisions import decide
        ev, notes = decide(root, cfg, args.subject, args.verdict, args.by, args.role, args.reason, args.feature_dir)
        _render(root, cfg)
        for n in notes:
            print(f"NOTE: {n}")
        print(f"auditGuard: recorded #{ev['seq']} {ev['kind']} {(ev.get('data') or {}).get('subject')} -> "
              f"{(ev.get('data') or {}).get('verdict')} by {(ev.get('actor') or {}).get('id')} ({ev['sprint']})")
        return EXIT_OK

    if cmd == "note":
        from .decisions import note
        ev = note(root, cfg, args.text, args.by, args.feature_dir)
        _render(root, cfg)
        print(f"auditGuard: recorded #{ev['seq']} note ({ev['sprint']} / {Path(ev['feature']).name if ev.get('feature') else PROJECT_KEY})")
        return EXIT_OK

    if cmd == "sprint":
        from .sprints import Register
        reg = Register.load(cfg.register_path)
        if args.action == "list":
            if args.json:
                print(json.dumps(reg.sprints, indent=2, ensure_ascii=False))
                return EXIT_OK
            if not reg.sprints:
                print("no sprints in the register - open one: auditguard sprint open <id> --by <name>")
            for s in reg.sprints:
                print(f"  {s['id']:<14} {s.get('status', ''):<8} {s.get('start') or '?'} .. {s.get('end') or '?'}  "
                      f"{s.get('name') or ''}" + (f"  seal {str((s.get('closed') or {}).get('seal'))[:12]}" if s.get("status") == "closed" else ""))
            return EXIT_OK
        if args.action == "current":
            cur = reg.current()
            print(cur["id"] if cur else "none (events go to _unassigned)")
            return EXIT_OK if cur else EXIT_PROBLEMS
        if args.action == "open":
            if not args.id:
                raise AuditGuardError("sprint open needs an id, e.g. auditguard sprint open S-2026-21 --by <name>")
            from .decisions import sprint_open
            ev = sprint_open(root, cfg, args.id, args.by, args.name, args.start, args.end, args.goal, args.close_current)
            _render(root, cfg)
            print(f"auditGuard: sprint {args.id} opened (#{ev['seq']} in {ev['sprint']}/_project)")
            return EXIT_OK
        from .decisions import sprint_close
        seal = sprint_close(root, cfg, args.id, args.by)
        _render(root, cfg)
        audit_rel = rel_path(cfg.audit_root, root)
        print(f"auditGuard: sprint {seal['sprint']} closed and sealed - seal {seal['hash']}")
        if seal.get("warnings"):
            for w in seal["warnings"]:
                print(f"  [WARN] {w['rule']}: {w['detail']}")
        print("Anchor it in git:")
        print(f"  git add {audit_rel} && git commit -m \"Close sprint {seal['sprint']} (seal {seal['hash'][:12]})\"")
        print(f"  auditguard anchor --sprint {seal['sprint']} --push")
        return EXIT_OK

    if cmd == "anchor":
        from .decisions import refuse_agent_context
        from .golden import anchor
        refuse_agent_context("anchor")
        from .context import RunContext
        by = RunContext(root, cfg).actor_human(args.by)["id"] if not is_ci() else (args.by or "ci")
        note, messages = anchor(root, cfg, sprint=args.sprint, push=args.push, by=by)
        if args.json:
            print(json.dumps({"note": note, "messages": messages}, indent=2))
        else:
            print(f"auditGuard {__version__} | anchor")
            for m in messages:
                print(f"  {m}")
        return EXIT_OK if not any("FAILED" in m for m in messages) else EXIT_PROBLEMS

    if cmd == "render":
        keys = None
        if args.feature_dir and not args.all:
            fd = resolve_feature_dir(root, args.feature_dir)
            keys = [fd.name] if fd else None
        written = _render(root, cfg, html=args.html, keys=keys, sprints=[args.sprint] if args.sprint and not args.all else None)
        print(f"auditGuard {__version__} | render | {len(written)} file(s) updated")
        for p in written[:50]:
            print(f"  {rel_path(p, root)}")
        return EXIT_OK

    if cmd == "show":
        from .show import show
        print(show(root, cfg, feature_arg=args.feature_dir, sprint=args.sprint, stage=args.stage, kind=args.kind,
                   actor=args.actor, open_only=args.open, as_json=args.json, all_features=args.all))
        return EXIT_OK

    if cmd == "verify":
        return _verify(root, cfg, args)

    if cmd == "check":
        from .check import evaluate_rules, render_results
        results = evaluate_rules(root, cfg, sprint=args.sprint, feature_arg=args.feature_dir)
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            scope = f"sprint {args.sprint}" if args.sprint else (f"feature {args.feature_dir}" if args.feature_dir else "all")
            print(render_results(results, scope))
        return EXIT_PROBLEMS if any(r["status"] == "fail" for r in results) else EXIT_OK

    if cmd == "export":
        from .export import build_pack
        from .render_html import pack_viewer
        from .store import Store
        if cfg.get("rules", "anchored_before_export"):
            from .golden import Golden
            from .verify import verify_tree
            internal = verify_tree(cfg.audit_root, cfg.register_path)
            g = Golden(root, cfg, offline=True)
            g.g6(internal)
            if (g.counts.get("G6") or {}).get("verified", 0) == 0 or any(f.get("sprint") == args.sprint for f in g.findings):
                raise AuditGuardError(f"sprint {args.sprint} is not anchored (rules.anchored_before_export) - "
                                      f"run: auditguard anchor --sprint {args.sprint} --push", EXIT_PROBLEMS)
        verification = _verification(root, cfg, golden=True, recompute=False, offline=True)
        store = Store(root, cfg)
        out = Path(args.out) if args.out else None
        if out is not None and not out.is_absolute():
            out = Path.cwd() / out
        target, n = build_pack(root, cfg, args.sprint, out, verification, pack_viewer(store, cfg, args.sprint))
        print(f"auditGuard {__version__} | export | {target} ({n} files) - internal {verification['internal']['status']}, "
              f"golden {(verification.get('golden') or {}).get('status', 'skipped')}")
        return EXIT_OK

    if cmd == "serve":
        from .serve import serve
        return serve(root, cfg, args.port, args.open)

    if cmd == "configure":
        from .configure import run_configure
        text, data = run_configure(root, cfg, args.dry_run)
        print(json.dumps(data, indent=2) if args.json else text)
        return EXIT_OK
    raise AuditGuardError(f"unknown command {cmd}")


def _verification(root: Path, cfg: Any, *, golden: bool, recompute: bool, offline: bool) -> dict:
    from .golden import Golden
    from .verify import verify_tree
    internal = verify_tree(cfg.audit_root, cfg.register_path)
    report = {"v": 1, "generated": now_iso(), "auditguard": __version__, "internal": internal}
    if golden:
        report["golden"] = Golden(root, cfg, offline=offline).run(internal, recompute=recompute)
    return report


def _verify(root: Path, cfg: Any, args: argparse.Namespace) -> int:
    from .golden import render as render_golden
    from .verify import render_internal
    if args.pack:
        from .export import verify_pack
        result = verify_pack(Path(args.pack))
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"auditGuard {__version__} | verify --pack | {args.pack} | {result['events']} events · {result['evidence']} evidence")
            print("\n".join(render_internal(result)))
            print("golden   : skipped (no repository - verify the anchor tag in the repository: auditguard verify --golden)")
            print(f"\nRESULT: {result['status'].upper()}")
        return EXIT_OK if result["status"] == "pass" else EXIT_PROBLEMS
    report = _verification(root, cfg, golden=args.golden, recompute=args.recompute, offline=args.offline)
    internal = report["internal"]
    golden = report.get("golden")
    write_json(cfg.audit_root / "verify-report.json", report) if cfg.audit_root.is_dir() else None
    ok = internal["status"] == "pass" and (golden is None or golden.get("status") in ("pass", "skipped"))
    if args.record:
        from .context import RunContext
        from .recorder import Recorder
        from .common import sha256_json
        rc = RunContext(root, cfg)
        Recorder(rc).record(None, {"kind": "ci.verified", "actor": rc.actor_agent() if is_ci() else rc.actor_human(None),
                                   "source": "ci" if is_ci() else "cli",
                                   "data": {"internal": internal["status"], "golden": (golden or {}).get("status", "skipped"),
                                            "report_sha256": sha256_json(report)}})
    if not args.no_render and cfg.audit_root.is_dir():
        try:
            _render(root, cfg, html=(cfg.audit_root / "viewer" / "index.html").is_file())
        except AuditGuardError:
            pass
    lines = [f"auditGuard {__version__} | verify{' --golden' if args.golden else ''} | journals {internal['journals']} · "
             f"events {internal['events']} · evidence {internal['evidence']}"]
    lines += render_internal(internal)
    if golden is not None:
        lines += render_golden(golden)
    else:
        lines.append("golden   : not run (add --golden)")
    failing = len([f for f in (golden or {}).get("findings") or [] if f.get("label") in ("mismatch", "unexplained_commit")])
    lines.append("")
    lines.append(f"RESULT: {'PASS' if ok else 'FAIL'} | {len(internal['problems'])} internal problem(s), {failing} golden finding(s) | "
                 f"report: {rel_path(cfg.audit_root / 'verify-report.json', root)}")
    text = "\n".join(lines)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("## auditGuard verification\n\n```text\n" + text + "\n```\n", encoding="utf-8")
    return EXIT_OK if ok else EXIT_PROBLEMS
