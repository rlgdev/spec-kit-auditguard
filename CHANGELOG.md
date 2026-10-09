# Changelog

All notable changes to auditGuard are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-09

The first field run showed `/speckit.plan` taking far longer with auditGuard than without. The cause was not the
record itself but what ran around it: every agent event is a process chain on every tool call (`pre_tool_use`) and
every turn (`stop`), each hook re-rendered the views and ran scopeGuard's report, and the hook commands asked the
agent for an environment variable it did not need. This release makes the lean configuration the default and keeps
the complete recorder one line away ([README: Profiles](README.md#profiles-light-and-full)).

### Added

- **`profile: light | full`** (`light` is the default). The profile sets `render.on_hook`, `sessions.record`, the
  new `events.stop`, `guard.enabled` and `collectors.scopeguard.report`; a key written explicitly in the config
  file wins over the profile and `configure` marks it `(pinned)`. `light` turns all five off: the 20 hooks record
  every command, what it changed, its outcome and the gate verdicts, waivers, sign-offs and workflow verdicts read
  from the siblings' files - and nothing else runs in the agent's loop. `full` is the 0.1 recorder.
- **`events.stop`**: the `stop` event handler has its own switch. Without it an open command is closed as
  `command.abandoned` by the next `before_` hook (source `hook:next-command`), by `session_end` when wired, or by
  `sprint close`.
- **`configure` edits the agent's event config**: for the nested-JSON files Spec Kit writes (Claude Code
  `.claude/settings.json`, Gemini, Qwen, Tabnine) it removes the auditGuard entries of switched-off events - only
  those; the siblings' and the user's own hooks stay - and reports which auditGuard events are wired (`agent
  events`, `wired in`). It never adds entries: for a profile that wants an event that is missing it prints
  `NOT WIRED` and the Spec Kit command that re-registers them (`specify extension disable auditguard && specify
  extension enable auditguard`, then `configure`). `configure --json` carries `profile`, `switches`, `events` and
  `events_wired`.
- The README section **Profiles: light and full**: what each profile records, what runs at every hook, tool call
  and turn in each, what `light` gives up, and how `configure` handles the events.

### Changed

- **The default profile is `light`.** A project upgrading from 0.1.0 with the 0.1 config file keeps the recorder:
  that file set `render.on_hook`, `sessions.record`, `guard.enabled` and `report` explicitly, and explicit keys win.
  Only `events.stop` is new and off; set `profile: full` to have it on, then run `configure`.
- `collect` always rebuilds the Markdown views (it is an explicit action, not a hook).
- The hook commands no longer ask the agent to set `AUDITGUARD_CONTEXT=agent`: one plain script call per hook. The
  variable protected nothing in a hook's own process and its PowerShell form caused retries; it stays honoured when a
  harness sets it for the agent's shells (`decide`, `note`, `sprint open|close`, `anchor` exit 3).
- The config template documents the profiles and shows the five switches commented out, with their value per
  profile. An empty section (`sessions:` with every key commented) keeps the defaults instead of clearing them.
- CI runs the integration tests against scopeGuard 0.4.1 and archiGuard 0.1.1, the versions the Guardians
  bundle 0.1.1 pins (was 0.4.0 and 0.1.0).
- The example project (`examples/orders`) runs the `full` profile; `tools/make-example.py` sets it.
- Specification amendments A17-A19 ([docs/specification.md](docs/specification.md)).

## [0.1.0] - 2026-10-06

First release: the specification "auditGuard for Spec Kit - Specification v1.0" ([docs/specification.md](docs/specification.md),
with the implementation amendments A1-A16) as a Spec Kit extension.

### Added

- **Recorder** on all ten Spec Kit commands: mandatory `before_*` / `after_*` hooks (20, generated from one template)
  write `command.started` / `command.finished` with the hashes of the design artefacts, the files changed (design and
  code), the outcome (`pass`, `fail`, `escalated`, `error`) and the actor; `/speckit.specify` is recorded in the
  feature it creates.
- **Agent events** through Spec Kit's dispatcher: `session_start` / `session_end` (sessions on every agent event),
  `stop` (a command that ended without its `after_` hook - an escalation - is closed as `command.abandoned`; a command
  spanning several turns is not) and `pre_tool_use` (the guard).
- **Stages** as data: `stage.entered`, `stage.completed` by milestones (`design.signed`, `handover.4-5`,
  `implement.approved`, `pr.approved`), `stage.reentered` when work goes back.
- **Hash-chained journals** per feature across sprints under `audit/sprints/<sprint>/<feature>/journal.jsonl`, with
  content-addressed evidence, file locks, a tail check before every append and LF/UTF-8 everywhere.
- **Collectors** for scopeGuard (read-only coverage report, iteration history, escalation notes), archiGuard (step
  verdicts with iteration history, escalation notes, sign-off and re-open, handover 4->5, test loop, saved reports),
  the waiver history (scope deferrals, conformance deviations, the decision ledger), Spec Kit workflow gate verdicts,
  git merges, and plug-in collectors.
- **Out-of-band change detection**: artefact and code changes no recorded command explains become `artefact.changed`
  with `after_signoff` / `after_handover`, the inferred command and the author of the committed version.
- **Human decisions**: `auditguard decide` (design, implement, pr, spec-change, escalation, workflow gate) and
  `note`, reserved for people; the guard blocks them and edits of `audit/` for agents.
- **Sprints**: the register `audit/sprints.yml`, `sprint open | close`, the seal, redirects after a seal,
  `_unassigned`, completeness rules (`auditguard check`, evaluated at close; enforced in `mode: enforce`).
- **Verification**: internal (chains, gaps, order, evidence, seals, register) and against the golden sources G1-G8
  (commits reachable, artefact hashes with `anchored_at`, unexplained commits, evidence authenticity, decisions vs
  the handover pins / standards lock / ledger / workflow runs, anchored seals, anchored chain heads, scopeGuard
  recompute in a worktree), every finding with a reproduce command; `audit/verify-report.json`.
- **Anchoring** in git: anchor notes (`refs/notes/auditguard`) with every chain head and seal, annotated (optionally
  signed) tags `audit/<sprint>`, `--push`.
- **Audit packs**: `export` writes a reproducible zip (journals, evidence, views, the viewer, chain links,
  verification, SHA256SUMS); `verify --pack` verifies it without the repository.
- **Views**: `trail.md`, `sprint.md`, `features/<feature>.md`, `index.md` (deterministic), `show` in the terminal,
  and the offline **HTML viewer** (overview matrix, the trail drawn as a chain by stage, decisions, waivers, evidence
  with compare, verification), `serve` for a live view.
- Repository governance for corporate use: `CODEOWNERS`, `SECURITY.md` (private vulnerability reporting),
  `CONTRIBUTING.md` (the family's conventions and release steps), Dependabot for the GitHub Actions;
  `tools/build.py --check` also checks the catalog's `provides` counts against the manifest. CI runs the tests once
  more with PyYAML installed (ubuntu, Python 3.13), as archiGuard does: `yamlio` prefers PyYAML when importable.
- `configure`, a GitHub Action, the `examples/orders` story (two sprints with the real scopeGuard and archiGuard
  engines, generated by `tools/make-example.py`), an end-to-end test against a real Spec Kit install.
- The engine never writes `__pycache__` into `.specify/extensions/auditguard/` (the launcher sets
  `sys.dont_write_bytecode`), and the release archive and the audit packs are byte-identical whichever OS builds
  them (`create_system = 3` on every zip entry).
