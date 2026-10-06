#!/usr/bin/env python3
"""Build examples/orders: a Spec Kit project taken through two sprints with the real scopeGuard and archiGuard
engines while auditGuard records every hook, gate report, waiver, escalation and human decision.

    python tools/make-example.py --archiguard-src ../spec-kit-archiguard --scopeguard-src ../spec-kit-scopeguard
    python tools/make-example.py ... --keep-repo /tmp/orders      # also keep the git repository (for verify --golden)

The story (clock faked, so the trail spans two sprints):
  S-2026-21  the BA hands over 001-place-order · /speckit.plan repairs two gaps · /speckit.tasks escalates and the lead
             architect decides · a ledger waiver is approved · /speckit.analyze · the tech lead signs the design ·
             the sprint is closed, sealed and anchored
  S-2026-22  /speckit.implement repairs two fitness violations · a person edits the signed plan outside any command ·
             /speckit.converge · implement approved · 002-refunds is specified and planned with a deferral · a workflow
             gate is approved · the pull request of 001 is approved
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE_CLOCK = '''
import datetime as _d, os as _os
class _FakeDateTime(_d.datetime):
    @classmethod
    def now(cls, tz=None):
        v = _os.environ.get("FAKE_NOW")
        if not v:
            return _d.datetime.now(tz) if False else super().now(tz)
        t = _d.datetime.fromisoformat(v)
        return t.replace(tzinfo=None) if tz is None else t.astimezone(tz)
_d.datetime = _FakeDateTime
'''


class Story:
    def __init__(self, work: Path, ag_src: Path, sg_src: Path, verbose: bool):
        self.p = work / "orders"
        self.ag_src = ag_src
        self.sg_src = sg_src
        self.verbose = verbose
        self.clock_dir = work / "clock"
        self.clock_dir.mkdir(parents=True, exist_ok=True)
        (self.clock_dir / "sitecustomize.py").write_text(FAKE_CLOCK, encoding="utf-8")
        self.now = "2026-10-06T08:00:00+02:00"
        self.remote = work / "origin.git"

    # ------------------------------------------------------------- plumbing
    def env(self, **extra: str) -> dict:
        env = dict(os.environ)
        env.update({"FAKE_NOW": self.now, "AUDITGUARD_NOW": self.now, "GIT_AUTHOR_DATE": self.now,
                    "GIT_COMMITTER_DATE": self.now, "PYTHONPATH": str(self.clock_dir), "AUDITGUARD_ACTOR": "",
                    "CI": "", "GITHUB_ACTIONS": "", "SPECIFY_FEATURE_DIRECTORY": "", "TZ": "Europe/Warsaw"})
        env.pop("AUDITGUARD_CONTEXT", None)
        env.update(extra)
        return env

    def at(self, stamp: str) -> None:
        self.now = stamp

    def run(self, *cmd: str, ok=(0,), **env: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(list(cmd), cwd=str(self.p), env=self.env(**env), capture_output=True, text=True)
        if self.verbose or proc.returncode not in ok:
            print(f"$ {' '.join(cmd)}  [{proc.returncode}]\n{proc.stdout}{proc.stderr}")
        if proc.returncode not in ok:
            raise SystemExit(f"step failed: {' '.join(cmd)}")
        return proc

    def ag(self, *args: str, ok=(0,)) -> subprocess.CompletedProcess:
        return self.run(sys.executable, ".specify/extensions/archiguard/scripts/python/archiguard.py", *args, ok=ok)

    def au(self, *args: str, ok=(0,), stdin: str = "", **env: str) -> subprocess.CompletedProcess:
        cmd = [sys.executable, ".specify/extensions/auditguard/scripts/python/auditguard.py", *args]
        if stdin:
            proc = subprocess.run(cmd, cwd=str(self.p), env=self.env(**env), input=stdin, capture_output=True, text=True)
            if self.verbose or proc.returncode not in ok:
                print(f"$ {' '.join(cmd)} <payload  [{proc.returncode}]\n{proc.stdout}{proc.stderr}")
            return proc
        return self.run(*cmd, ok=ok, **env)

    def hook(self, event: str, feature: str = "") -> None:
        args = ["hook", event, "--via", "hooks"] + (["--feature-dir", feature] if feature else [])
        self.au(*args, AUDITGUARD_CONTEXT="agent")

    def git(self, *args: str, ok=(0,), author: str = "Roman <roman@example.com>") -> None:
        name, mail = author.split(" <")
        self.run("git", "-c", f"user.name={name}", "-c", f"user.email={mail.rstrip('>')}", *args, ok=ok)

    def commit(self, message: str, author: str = "Roman <roman@example.com>", paths=("-A",)) -> None:
        self.git("add", *paths)
        self.git("commit", "-q", "-m", message, ok=(0, 1), author=author)

    def write(self, rel: str, text: str) -> None:
        path = self.p / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.touch(path)

    def touch(self, *paths: Path) -> None:
        from datetime import datetime
        ts = datetime.fromisoformat(self.now).timestamp()
        for path in paths:
            if path.is_dir():
                for p in path.rglob("*"):
                    if p.is_file():
                        os.utime(p, (ts, ts))
            elif path.is_file():
                os.utime(path, (ts, ts))

    def settle(self, feature: str = "specs/001-place-order") -> None:
        """Gate files written by a sibling get the faked time as their modification time."""
        f = self.p / feature
        self.touch(f / "gates", f / ".scopeguard", *f.glob("scopeguard-*.md"), self.p / ".specify" / "archiguard")

    def session(self, event: str, sid: str) -> None:
        payload = {"hook_event_name": {"session_start": "SessionStart", "session_end": "SessionEnd", "stop": "Stop"}[event],
                   "session_id": sid, "source": "startup"}
        self.au("event", event, stdin=json.dumps(payload))

    # ---------------------------------------------------------------- setup
    def setup(self) -> None:
        src = self.ag_src / "examples" / "orders"
        self.p.mkdir(parents=True)
        shutil.copytree(src / ".specify" / "standards", self.p / ".specify" / "standards")
        (self.p / ".specify" / "archiguard").mkdir(parents=True)
        shutil.copy(src / "pom.xml", self.p / "pom.xml")
        self.write(".specify/init-options.json", json.dumps({"ai": "claude", "script": "sh", "integration": "claude"}) + "\n")
        self.write(".specify/memory/constitution.md", "# Orders constitution\n\n## Principle I - Test first\n\nEvery acceptance criterion has a test.\n\n"
                   "## Principle II - Contracts before code\n\nEvery HTTP API is published as an OpenAPI contract before it is implemented.\n")
        self.write(".gitignore", "target/\n.specify/auditguard/state/\n__pycache__/\n")
        # the three extensions, as `specify extension add` would place them
        ext = self.p / ".specify" / "extensions"
        ag = ext / "archiguard"
        ag.mkdir(parents=True)
        for name in ("extension.yml", "config-template.yml", "scope-config-template.yml", "package-policy.yml"):
            shutil.copy(self.ag_src / name, ag / name)
        for d in ("scripts", "manifests", "templates", "commands"):
            shutil.copytree(self.ag_src / d, ag / d, ignore=shutil.ignore_patterns("__pycache__"))
        cfg = (self.ag_src / "config-template.yml").read_text(encoding="utf-8")
        cfg = (cfg.replace("  rulebook: null ", "  rulebook: acme-standards@v2026.10.1 ")
               .replace("  profile: null ", "  profile: java-service ")
               .replace("  pin: null ", '  pin: "1.4.0" ')
               .replace("integration: inline", "integration: hooks"))
        (ag / "archiguard-config.yml").write_text(cfg, encoding="utf-8")
        shutil.copy(self.ag_src / "scope-config-template.yml", ag / "scope-config.yml")
        sg = ext / "scopeguard"
        sg.mkdir(parents=True)
        for name in ("extension.yml", "config-template.yml"):
            shutil.copy(self.sg_src / name, sg / name)
        shutil.copytree(self.sg_src / "scripts", sg / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        (sg / "scopeguard-config.yml").write_text("integration: embedded\n", encoding="utf-8")
        au = ext / "auditguard"
        au.mkdir(parents=True)
        for name in ("extension.yml", "config-template.yml"):
            shutil.copy(ROOT / name, au / name)
        for d in ("scripts", "templates", "commands"):
            shutil.copytree(ROOT / d, au / d, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / "config-template.yml", au / "auditguard-config.yml")
        self.write(".specify/extensions.yml", "installed:\n  - archiguard\n  - scopeguard\n  - auditguard\nhooks: {}\n")
        self.run("git", "init", "-q", "-b", "main")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "tag.gpgsign", "false")
        self.run("git", "init", "-q", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.ag("resolve")
        self.au("configure")
        self.commit("Orders service: constitution, standards, Spec Kit with scopeGuard, archiGuard and auditGuard")
        self.git("push", "-q", "-u", "origin", "main")

    # ---------------------------------------------------------------- story
    def sprint_21(self) -> None:
        f = "specs/001-place-order"
        src = self.ag_src / "examples" / "orders" / f
        self.at("2026-10-06T08:40:00+02:00")
        self.au("sprint", "open", "S-2026-21", "--name", "Sprint 21", "--start", "2026-10-06", "--end", "2026-10-17",
                "--goal", "Orders: place an order, designed and signed", "--by", "Roman")
        self.at("2026-10-06T09:00:00+02:00")
        self.git("checkout", "-q", "-b", "001-place-order")
        for name in ("spec.md", "handover.yml"):
            self.write(f"{f}/{name}", (src / name).read_text(encoding="utf-8"))
        self.write(".specify/feature.json", json.dumps({"feature_directory": f}) + "\n")
        self.commit("T000 UC-001 formal handover from the BA specification tool", author="BA team <ba@example.com>")

        # /speckit.plan: two gaps found and repaired
        self.at("2026-10-06T09:12:00+02:00")
        self.session("session_start", "s-1006-a")
        self.hook("before_plan")
        self.ag("run", "plan", "a")
        self.settle()
        self.at("2026-10-06T09:31:00+02:00")
        for name in ("plan.md", "data-model.md"):
            self.write(f"{f}/{name}", (src / name).read_text(encoding="utf-8"))
        shutil.copytree(src / "contracts", self.p / f / "contracts", dirs_exist_ok=True)
        self.ag("run", "plan", "b", ok=(1,))
        self.settle()
        self.at("2026-10-06T09:44:00+02:00")
        plan = (self.p / f / "plan.md").read_text(encoding="utf-8")
        plan = plan.replace("<!-- ARCH-201 (layering) is missing on purpose: the A3.3 check-plan finds it. -->",
                            "| ARCH-201 | Layers depend downwards only (api -> application -> domain) | satisfied | Project Structure | |")
        plan = plan.replace("<!-- BR-002 (request the payment) is missing on purpose: the scope gate finds it when it is plugged in. -->",
                            "| BR-002 | Request the payment of the order total | covered | Integration Points (payments) | |")
        self.write(f"{f}/plan.md", plan)
        self.ag("run", "plan", "b")
        self.settle()
        self.at("2026-10-06T09:46:00+02:00")
        self.hook("after_plan")
        self.commit("T000 UC-001 plan", author="Roman <roman@example.com>")
        self.session("session_end", "s-1006-a")

        # /speckit.tasks: escalates, the lead architect decides, second run passes
        self.at("2026-10-07T09:02:00+02:00")
        self.session("session_start", "s-1007-a")
        self.hook("before_tasks")
        tasks = (src / "tasks.md").read_text(encoding="utf-8")
        broken = tasks.replace("- [ ] T007 [FITNESS] Verify the layering - ARCH-201\n", "")
        broken = broken.replace("- [x]", "- [ ]")
        self.write(f"{f}/tasks.md", broken)
        self.ag("run", "tasks", "b", ok=(1,))
        self.settle()
        self.at("2026-10-07T09:20:00+02:00")
        self.ag("run", "tasks", "b", ok=(3,))
        self.settle()
        note = self.p / f / "gates" / "escalation-tasks-b.md"
        if note.is_file():
            text = note.read_text(encoding="utf-8").replace(
                "TODO(agent)", "The layering rule ARCH-201 has no fitness task: the profile java-service has no import check "
                "for the new module yet. Decision needed: add the task with the generic layering check, or waive ARCH-201.", 1)
            note.write_text(text, encoding="utf-8")
            self.touch(note)
        self.at("2026-10-07T09:21:00+02:00")
        self.session("stop", "s-1007-a")
        self.at("2026-10-07T10:30:00+02:00")
        self.au("decide", "escalation:escalation-tasks-b.md", "accept", "--by", "Roman", "--role", "lead architect",
                "--reason", "Add the layering fitness task with the generic import check; ARCH-201 stays in scope",
                "--feature-dir", f)
        self.at("2026-10-07T11:05:00+02:00")
        self.hook("before_tasks")
        self.write(f"{f}/tasks.md", tasks.replace("- [x]", "- [ ]"))
        self.ag("run", "tasks", "b")
        self.settle()
        self.at("2026-10-07T11:12:00+02:00")
        self.hook("after_tasks")
        self.commit("T000 UC-001 tasks")
        self.session("session_end", "s-1007-a")

        # a ledger waiver, approved by the lead architect
        self.at("2026-10-07T14:00:00+02:00")
        self.ag("ledger", "add", "--id", "WVR-0012", "--type", "waiver", "--title",
                "Payments sandbox credentials stay in the test configuration until the vault client ships",
                "--rule", "ARCH-401", "--owner", "Orders tech lead", "--approver", "Lead architect",
                "--expires", "2026-10-31", "--status", "approved", "--feature", "001-place-order")
        self.commit("T000 UC-001 WVR-0012 approved by the lead architect", paths=(".specify/archiguard/ledger.jsonl",))

        # /speckit.analyze, then the tech lead signs the design
        self.at("2026-10-08T09:00:00+02:00")
        self.session("session_start", "s-1008-a")
        self.hook("before_analyze")
        self.write(f"{f}/gates/analyze-report.md",
                   "## Specification Analysis Report\n\n| ID | Category | Severity | Location(s) | Summary | Recommendation |\n"
                   "|----|----|----|----|----|----|\n| A1 | Terminology | LOW | plan.md | 'order line' and 'item' both used | use 'order line' |\n\n"
                   "- Critical Issues Count: 0\n")
        self.at("2026-10-08T09:06:00+02:00")
        self.hook("after_analyze")
        self.session("session_end", "s-1008-a")
        self.commit("T000 UC-001 analysis report")
        self.at("2026-10-08T14:00:00+02:00")
        self.ag("signoff", "--by", "Tech lead", "--role", "tech lead", "--feature-dir", f)
        self.settle()
        self.commit("T000 UC-001 design signed by the tech lead", author="Tech lead <techlead@example.com>")
        self.au("collect", "--feature-dir", f)
        self.git("push", "-q", "-u", "origin", "001-place-order")

        # close, seal and anchor the sprint
        self.at("2026-10-17T17:00:00+02:00")
        self.commit("T000 UC-001 audit trail of S-2026-21")
        self.au("sprint", "close", "S-2026-21", "--by", "Roman")
        self.commit("T000 UC-001 close sprint S-2026-21")
        self.au("anchor", "--sprint", "S-2026-21", "--by", "Roman")
        self.git("push", "-q", "origin", "001-place-order")
        self.git("push", "-q", "origin", "refs/notes/auditguard", "refs/tags/audit/S-2026-21")

    def sprint_22(self) -> None:
        f = "specs/001-place-order"
        src = self.ag_src / "examples" / "orders"
        self.at("2026-10-20T08:30:00+02:00")
        self.au("sprint", "open", "S-2026-22", "--name", "Sprint 22", "--start", "2026-10-20", "--end", "2026-10-31",
                "--goal", "Orders implemented and approved; refunds designed", "--by", "Roman")
        # /speckit.implement: two fitness violations repaired
        self.at("2026-10-20T09:00:00+02:00")
        self.session("session_start", "s-1020-a")
        self.hook("before_implement")
        self.ag("run", "implement", "a")
        self.settle()
        self.at("2026-10-20T10:40:00+02:00")
        shutil.copytree(src / "src", self.p / "src", dirs_exist_ok=True)
        self.touch(self.p / "src")
        self.commit("T004 UC-001 OrderController and PlaceOrder", author="Claude agent <agent@example.com>")
        self.ag("run", "implement", "b", ok=(1,))
        self.settle()
        self.at("2026-10-20T11:15:00+02:00")
        order = self.p / "src/main/java/com/acme/orders/domain/Order.java"
        order.write_text("\n".join(l for l in order.read_text(encoding="utf-8").split("\n") if "orders.api" not in l), encoding="utf-8")
        ctrl = self.p / "src/main/java/com/acme/orders/api/OrderController.java"
        ctrl.write_text(ctrl.read_text(encoding="utf-8").replace("com.acme.payments.internal.PaymentGateway", "com.acme.payments.api.PaymentsClient")
                        .replace("PaymentGateway", "PaymentsClient"), encoding="utf-8")
        self.touch(order, ctrl)
        tasks = (self.p / f / "tasks.md").read_text(encoding="utf-8").replace("- [ ]", "- [x]")
        self.write(f"{f}/tasks.md", tasks)
        self.ag("run", "implement", "b", ok=(0, 1))
        self.settle()
        self.at("2026-10-20T11:20:00+02:00")
        self.hook("after_implement")
        self.commit("T005-T009 UC-001 payment request, fitness tasks", author="Claude agent <agent@example.com>")
        self.session("session_end", "s-1020-a")

        # a person edits the signed plan outside any command
        self.at("2026-10-21T10:00:00+02:00")
        plan = self.p / f / "plan.md"
        plan.write_text(plan.read_text(encoding="utf-8") + "\n## Notes\n\nPayment retries are handled by the payments context.\n", encoding="utf-8")
        self.touch(plan)
        self.commit("T000 UC-001 clarify payment retries in the plan", author="J. Doe <jdoe@example.com>")
        self.at("2026-10-21T11:00:00+02:00")
        self.session("session_start", "s-1021-a")
        self.hook("before_converge")
        self.at("2026-10-21T11:08:00+02:00")
        self.hook("after_converge")
        self.session("session_end", "s-1021-a")
        self.at("2026-10-21T15:00:00+02:00")
        self.au("decide", "implement", "approve", "--by", "Tech lead", "--role", "tech lead",
                "--reason", "fitness green, converge clean; the plan note is editorial", "--feature-dir", f)
        self.commit("T000 UC-001 audit trail")
        self.git("push", "-q", "origin", "001-place-order")

        # 002-refunds: specified and planned, one requirement deferred
        g = "specs/002-refunds"
        self.at("2026-10-22T09:00:00+02:00")
        self.session("session_start", "s-1022-a")
        self.git("checkout", "-q", "-b", "002-refunds")
        self.hook("before_specify")
        self.write(f"{g}/spec.md", "# Feature Specification: Refunds\n\n### User Story 1 - Refund a cancelled order (Priority: P1)\n\n"
                   "A customer who cancels an order gets the payment back.\n\n## Requirements\n\n"
                   "- **FR-001**: System MUST refund the full order total when an order is cancelled.\n"
                   "- **FR-002**: System MUST record every refund with its payment reference.\n"
                   "- **FR-003**: System MUST support partial refunds of single order lines.\n")
        self.write(".specify/feature.json", json.dumps({"feature_directory": g}) + "\n")
        self.at("2026-10-22T09:14:00+02:00")
        self.hook("after_specify")
        self.at("2026-10-22T10:00:00+02:00")
        self.hook("before_plan")
        self.write(f"{g}/plan.md", "# Implementation Plan: Refunds\n\n## Scope Coverage\n\n"
                   "| ID | Title | Status | Plan reference | Reason (required if deferred) |\n|----|----|----|----|----|\n"
                   "| US1 | Refund a cancelled order (P1) | covered | contracts/refunds.yaml POST /refunds | |\n"
                   "| FR-001 | Full refund on cancellation | covered | data-model.md Refund | |\n"
                   "| FR-002 | Refund record with payment reference | covered | data-model.md Refund.paymentRef | |\n"
                   "| FR-003 | Partial refunds | deferred | | PO decision 2026-10-22: partial refunds move to phase 2 |\n")
        self.write(f"{g}/data-model.md", "# Data model\n\n## Refund\n\n- id, orderId, amount, paymentRef\n")
        self.at("2026-10-22T10:25:00+02:00")
        self.hook("after_plan", g)
        self.commit("Specification and plan of 002-refunds")
        self.session("session_end", "s-1022-a")
        # a workflow run: the spec review gate approved by the tech lead
        runs = self.p / ".specify" / "workflows" / "runs" / "a1b2c3d4"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "state.json").write_text(json.dumps({
            "run_id": "a1b2c3d4", "workflow_id": "archiguard-sdd", "status": "paused", "current_step_id": "plan",
            "step_results": {"review-spec": {"type": "gate", "output": {"message": "Review the generated spec before planning.",
                                                                          "options": ["approve", "reject"], "choice": "approve"},
                                             "status": "completed", "error": None}},
            "created_at": "2026-10-22T07:05:00+00:00", "updated_at": "2026-10-22T07:40:00+00:00"}, indent=2), encoding="utf-8")
        (runs / "inputs.json").write_text(json.dumps({"inputs": {"feature": g, "design_authority": "Tech lead"}}), encoding="utf-8")
        (runs / "log.jsonl").write_text(json.dumps({"event": "step_completed", "step_id": "review-spec", "status": "completed",
                                                    "timestamp": "2026-10-22T07:40:00+00:00"}) + "\n", encoding="utf-8")
        self.at("2026-10-22T11:00:00+02:00")
        self.au("collect", "--feature-dir", g)
        self.commit("Audit trail")
        self.git("push", "-q", "-u", "origin", "002-refunds")

        # the pull request of 001 is approved
        self.at("2026-10-23T09:30:00+02:00")
        self.au("decide", "pr", "approve", "--by", "Tech lead", "--role", "tech lead",
                "--reason", "architecture compliance report and tests green", "--feature-dir", f)
        self.au("note", "Refunds planning continues next sprint with the payments team", "--by", "Roman", "--feature-dir", g)
        self.commit("Audit trail")
        self.git("push", "-q", "origin", "002-refunds")
        self.au("anchor", "--by", "Roman")
        self.git("push", "-q", "-f", "origin", "refs/notes/auditguard")
        self.at("2026-10-23T09:40:00+02:00")
        self.au("verify", "--golden", "--recompute")
        self.au("render", "--html")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archiguard-src", default=os.environ.get("ARCHIGUARD_SRC"), help="checkout of spec-kit-archiguard")
    parser.add_argument("--scopeguard-src", default=os.environ.get("SCOPEGUARD_SRC"), help="checkout of spec-kit-scopeguard")
    parser.add_argument("--out", default=str(ROOT / "examples" / "orders"), help="where the example goes (replaced)")
    parser.add_argument("--keep-repo", help="also copy the full git repository (with history, notes and tags) here")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not args.archiguard_src or not args.scopeguard_src:
        parser.error("--archiguard-src and --scopeguard-src (or ARCHIGUARD_SRC / SCOPEGUARD_SRC) are required")
    with tempfile.TemporaryDirectory() as tmp:
        story = Story(Path(tmp), Path(args.archiguard_src).resolve(), Path(args.scopeguard_src).resolve(), args.verbose)
        story.setup()
        story.sprint_21()
        story.sprint_22()
        if args.keep_repo:
            keep = Path(args.keep_repo)
            origin_copy = keep.parent / (keep.name + "-origin.git")
            for old in (keep, origin_copy):
                if old.exists():
                    shutil.rmtree(old)
            shutil.copytree(story.p, keep, symlinks=True)
            shutil.copytree(story.remote, keep.parent / (keep.name + "-origin.git"))
            subprocess.run(["git", "remote", "set-url", "origin", str(keep.parent / (keep.name + "-origin.git"))], cwd=keep)
        out = Path(args.out)
        if out.exists():
            shutil.rmtree(out)
        ignore = shutil.ignore_patterns(".git", "state", "__pycache__", "scripts", "commands", "manifests", "templates",
                                        "standards.lock.yml.bak")
        shutil.copytree(story.p, out, ignore=ignore)
        # the extension engines are not part of the example (they are installed with `specify extension add`)
        for ext in ("archiguard", "scopeguard", "auditguard"):
            d = out / ".specify" / "extensions" / ext
            for p in list(d.iterdir()) if d.is_dir() else []:
                if p.name not in ("archiguard-config.yml", "scope-config.yml", "scopeguard-config.yml", "auditguard-config.yml"):
                    if p.is_dir():
                        shutil.rmtree(p)
                    else:
                        p.unlink()
        print(f"example written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
