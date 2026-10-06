# auditGuard for Spec Kit — Specification v1.0 (ready to implement)

> **Status:** implemented in auditGuard 0.1.0. The amendments below were made while implementing and testing against
> Spec Kit 1.0.1 / 1.0.13, scopeGuard 0.4.0 and archiGuard 0.1.0; where the body below differs, the amendment wins.

## Amendments in the 0.1.0 implementation

| # | Requirement | Amendment | Why |
|---|-------------|-----------|-----|
| A1 | FR-202 | `seq` counts the events of a **chain** (feature or `_project`) across all sprint folders, not per journal file; `prev` is the hash of the event with the previous `seq`, wherever it is stored. | One rule instead of two; a chain that moves between `_unassigned` and sprints stays linear; a deleted sprint folder is a gap like a deleted line. |
| A2 | §7.1 | Four event commands (`speckit.auditguard.sessionstart`, `stop`, `sessionend`, `guard`) instead of one guard command for all events: **28 commands**. | Spec Kit's events dispatcher passes only the payload, not the event name; payloads differ per agent. |
| A3 | FR-004 | `stop` closes an open command at once only when it escalated or errored. Otherwise it marks the command stopped; the next `before_` hook, `session_end`, `collect` or `sprint close` closes it as `command.abandoned` **at the time of the stop**. | Claude Code fires `Stop` at the end of every turn; `/speckit.clarify` and `/speckit.specify` span several turns. |
| A4 | FR-005 | A further outcome **`fail`**: the command's own archiGuard step-B verdict (`gates/<cmd>-b.json`) changed during the command and ended with blocking findings. | A command that finished with open findings is not `pass`. |
| A5 | FR-002, §5.1 | `artefacts` keys and `changed` paths are **repository-relative**; `changed` is a list of `{path, from, to}` covering design artefacts and code; new field `worktree` (code files with uncommitted changes). | G2 / G3 compare directly with git paths; code changes inside a command are part of its record. |
| A6 | FR-002 | `/speckit.specify` creates the feature, so its `command.started` is written at `after_specify` (with the original start time). | Before the command there is no feature to attach it to. |
| A7 | §5.2 | New kinds: `escalation.withdrawn` (the note disappeared because the gate passed later), `report.saved`, `test.exit` (milestone, from archiGuard's test loop), `collector.ran`; ADR status changes in the ledger are `decision` events with subject `ledger:<id>`. | Observed in archiGuard's real output. |
| A8 | §5.5, FR-607 | The anchor note also lists `chains: {chain: {seq, hash}}`; G7 checks chain heads. | Chain heads, not files, are what a truncation changes. |
| A9 | FR-501 | Code reconciliation compares with a **workstation baseline** (`.specify/auditguard/state/baseline.json`); design artefacts compare with the chain. G3 is the authoritative check for code. | Code is project-wide; comparing it with a feature chain re-reported old changes when switching features. |
| A10 | §7.3 | New keys `golden.git.exclude_paths`, `collectors.git.project_artefacts`, `collectors.scopeguard.timeout`; `explain_paths` no longer contains `audit/**`. | The gates' own output and the trail are not "work" to explain. |
| A11 | FR-602, FR-604 | The event-level `anchored_at` is the commit where **every** recorded file has the recorded content (G8 recomputes there). G4 labels a never-committed source `unanchored`, an overwritten one `ephemeral`. | Precision of the recompute. |
| A12 | FR-6xx | Git fields and golden checks require the Spec Kit project to be the root of its git repository; otherwise they are skipped with that reason. | Paths in the trail are project-relative. |
| A13 | FR-901 | CLI additions: `event <name>` (the runtime events, payload on stdin; `guard` is `event pre_tool_use`), `verify --offline`, `--no-render` and `--out`. | Used by the event commands and CI. |
| A14 | FR-904 | `configure` switches the 20 hooks in `.specify/extensions.yml` according to `integration`, creates an empty `audit/sprints.yml` when missing (there is no template), adds the `.gitattributes` lines and the workstation `.gitignore`, and prints the table. It does **not** switch the 4 agent events: Spec Kit wires them at `specify extension add`; `configure` only reports whether they are wired and whether `guard.enabled` / `sessions.record` are on. | The events dispatcher owns the agent settings file. |
| A15 | FR-903, §7.3, §7.4 | `local-config.yml` may override `integration`, `render.on_hook`, `render.html_on_hook` and `viewer.*`. `collect` has no `--since` (it is the plug-in collector contract of FR-408 only); `verify` also has `--no-render` (used by the action). | What the implementation exposes. |
| A16 | §7.5 | `action.yml` inputs: `command` (`verify` default, `check`, `render`, `anchor`), `golden`, `recompute`, `sprint` (default empty = chain heads only, for `anchor`), `push`, `summary`, `engine`, `working-directory`, `args`. The action uploads no artefact (the viewer is published with `actions/upload-pages-artifact`, see `docs/ci.md`) and has no `record` input (`args: "--record"`). Wiring in `docs/ci.md`: pull requests and main → `verify --golden`; main additionally → `anchor --push`. | The published action. |


| | |
|---|---|
| Extension id | `auditguard` · repository `rlgdev/spec-kit-auditguard` · MIT |
| Family | Guardians — third member after scopeGuard (0.4.x) and archiGuard (0.1.x); same engine shape, launchers, config and release conventions |
| Host | GitHub Spec Kit ≥ 1.0.1 (hooks on all ten commands, agent runtime events, workflow engine) · Python ≥ 3.9, standard library only |
| Supersedes | DESIGN.md v0.1 (2026-10-06); every open decision of that draft is taken in §0.2 |
| Traceability | User stories `US1–US9`, functional requirements `FR-###`, non-functional `NFR-###`, success criteria `SC-###`. The ids are stable; scopeGuard can trace this document (`ids.requirement_prefixes: [FR, NFR, SC]`) |

---

## 0. Summary and decisions

### 0.1 What auditGuard delivers

A tamper-evident audit trail of the AI-native SDLC that is **written by a script, never by the agent**, and
**verifiable against the golden sources** of the organisation. For every Spec Kit feature it records each
command run (start, end, outcome, artefact hashes), marks the stage it belongs to (Design → Implement →
Test), snapshots the gate reports of scopeGuard and archiGuard at that moment, keeps the history of every
waiver and deferral, records every human decision (sign-offs, workflow gate verdicts, escalation answers,
sprint open/close) and every artefact change that happened outside a recorded command. The trail lives in
`audit/`, partitioned by sprint and by feature, as hash-chained JSON Lines plus generated Markdown and a
self-contained HTML viewer. A closed sprint is sealed, anchored in git and exportable as an audit pack that
verifies offline.

### 0.2 Decisions taken (from DESIGN.md §10, plus the new asks)

| # | Decision | Taken |
|---|----------|-------|
| D1 | Partitioning | **Sprint-first folders** (`audit/sprints/<sprint>/<feature>/`), **one hash chain per feature across sprints** (cross-sprint `prev` link), sprint sealing |
| D2 | Sprint source of truth | **Manual register** `audit/sprints.yml`; a tracker collector (Jira) may propose entries later, never write them |
| D3 | Default mode | **`record`** — never blocks; `enforce` turns the completeness rules into failures of `check` and CI |
| D4 | PR approval | **Human** `auditguard decide pr approve --by …` by default; the `git` golden collector additionally records merge commits; CI may record reviewers through a plug-in collector |
| D5 | Design completion without archiGuard | **`auditguard decide design approve --by …`**; with archiGuard, `signoff.json` is the milestone |
| D6 | Stage names | **`design` / `implement` / `test`** = A3 / A4 / A5 of the loop |
| D7 | Golden sources (new) | **git repository** (artefacts, code, history), **BA specification** through the handover record's pins, **standards** through the lock, **decision ledger**, **workflow run state**, optional **tracker**. `verify --golden` re-derives every claim of the trail from them; `anchor` writes chain heads and seals into git (notes + annotated, optionally signed, tags) |
| D8 | User interface (new) | **Static single-file viewer** (`audit/viewer/index.html` + regenerated `data.js`; vanilla HTML/CSS/JS, no network, works from `file://`), `auditguard serve` for a live local view, `auditguard show` for the terminal, export pack carries the viewer |
| D9 | Out-of-band change detection and sealing (promoted) | Core features, on by default (FR-5xx, FR-3xx) |

---

## 1. Scope, actors, glossary

**In scope.** Recording, storage, verification (internal and against golden sources), sealing, anchoring,
export, rendering (Markdown, HTML viewer, terminal), the agent guard, the collectors for git, scopeGuard,
archiGuard and Spec Kit workflow runs, the plug-in collector contract, configuration, packaging as a Spec
Kit extension with hooks and events, a GitHub Action.

**Out of scope (v1).** Writing to external systems (Jira, Confluence); recording the agent's prompts or
tool calls; a server-side multi-project portal; signing with anything other than git's own tag signing.

**Actors.** *Agent* — the coding agent running Spec Kit commands (Claude Code first; harness-neutral
through Spec Kit). *Tech lead* — design authority, signs plans and approves pull requests. *Lead
architect* — owns standards, ledger, `audit/` (CODEOWNERS). *Developer* — runs the loop. *Auditor* —
reads and verifies the trail, possibly offline from an export pack. *CI* — verifies on every push, anchors
on the main branch.

**Glossary.** *Feature* — a Spec Kit feature directory `specs/NNN-slug` (the scope unit). *Sprint* — a
time box in the register. *Journal* — the chained JSON Lines file of one feature in one sprint. *Event* —
one journal line. *Evidence* — a content-addressed snapshot of a file referenced by an event. *Golden
source* — a system the organisation treats as authoritative (git, BA specification repository, standards
repository, decision ledger, tracker); the trail is a set of claims about them. *Anchor* — a git note or
tag carrying a journal head hash or a seal hash. *Seal* — the record that closes a sprint. *Stage* —
design, implement or test. *Milestone* — the event that completes a stage.

---

## 2. User stories

Priorities: P1 must ship in 0.1.0, P2 in 0.1.x, P3 in 0.2.

### US1 — Record every command of the loop (P1)
As a lead architect, I want every Spec Kit command run on a feature recorded with who ran it, when, what it
changed and how it ended, so that the trail of a feature is complete without anyone writing it by hand.

*Acceptance.* **Given** auditGuard is installed with `integration: hooks`, **when** `/speckit.plan` runs
on `specs/001-x`, **then** the journal of the current sprint for `001-x` gains `command.started` (before)
and `command.finished` (after) with the sha256 of every design artefact before and after, the list of
changed files, the outcome and the actor. **Given** the command ends in an escalation (no `after_` hook),
**when** the agent's turn stops, **then** the open command is closed as `command.abandoned` with
`outcome: escalated` and the escalation note as evidence.

### US2 — See the stages, not the commands (P1)
As a tech lead, I want the trail grouped by Design, Implement and Test with explicit markers when a stage
was entered and completed, so that I can see where a feature stands without reading slash commands.

*Acceptance.* **Given** `tasks` passed and `signoff.json` was written, **when** the trail renders, **then**
Design shows `✔ signed <date> by <name>` and Implement shows `○` until `before_implement` fires.

### US3 — Keep the gate reports and the waiver history (P1)
As an auditor, I want the scopeGuard and archiGuard reports as they were at each gate, and the full
history of every deferral and waiver (added, changed, approved, revoked, expired), so that I can see what
was waived, by whom, and whether the waiver was still valid when the code shipped.

*Acceptance.* **Given** plan.md defers `FR-007` with a reason and later removes the deferral, **then** the
trail has `waiver.added` and `waiver.removed` with the reason text and the plan rows as evidence.
**Given** the ledger gains `WVR-0012 approved`, **then** `waiver.approved` names owner, approver, expiry.

### US4 — Record the human in the loop (P1)
As a lead architect, I want every human decision — design sign-off and re-open, workflow gate verdicts,
escalation answers, PR approval, sprint open/close — recorded with the person, role and reason, and I want
the agent to be unable to record one.

*Acceptance.* **Given** the agent runs `auditguard decide design approve --by X` from its shell, **then**
the guard blocks it with a message; **given** a person runs the same command in a terminal, **then** a
`decision` event with `actor.type: human` is appended.

### US5 — Detect what happened outside the hooks (P1)
As a tech lead, I want every change to a design artefact or to code that no recorded command explains to
be flagged — a hand edit of a signed plan, a command run with hooks skipped — so that the trail shows
what the agent did *and* what it did not do.

*Acceptance.* **Given** a person edits `plan.md` after `signoff.json` exists, **when** the next hook or
`collect` runs, **then** `artefact.changed` with `after_signoff: true` is appended and rule
`no_changes_after_signoff` is reported.

### US6 — Close and seal a sprint, export an audit pack (P1)
As a lead architect, I want to close a sprint so that its folder is sealed, anchored in git and exportable
as a zip that an auditor can verify offline.

*Acceptance.* **Given** sprint `S-2026-21` is open, **when** I run `sprint close --by Roman` then commit
and `anchor`, **then** `seal.json` exists, the register says `closed`, tag `audit/S-2026-21` points at
the commit containing the seal, and `export --sprint S-2026-21` produces a zip whose
`verify --pack` passes on a machine without the repository.

### US7 — Verify the trail against the golden sources (P1)
As an auditor, I want to verify that every claim in the trail agrees with the golden sources — the
commits exist, the recorded artefact hashes match the files in those commits, every commit that touched
the feature is explained by an event, the sign-off hashes match the signed files, the spec hash matches
the BA handover pins, the seals match their anchors — so that the trail cannot be a story told by the
tool alone.

*Acceptance.* **Given** someone rewrites `plan.md` in a commit without a recorded command, **when**
`verify --golden` runs, **then** finding `unexplained_commit` names the commit, the author and the file.
**Given** an event records a plan hash that was never committed, **then** the event is marked
`ephemeral`, not `verified`.

### US8 — View the trail (P1 Markdown and terminal, P2 HTML viewer)
As a tech lead or auditor, I want to open the trail of a sprint or a feature, filter by stage, actor or
kind, open the evidence, and see the verification status of each event, without installing anything and
without network access.

*Acceptance.* **Given** `render --html` ran, **when** I open `audit/viewer/index.html` from the file
system in Chrome, Edge or Firefox with no network, **then** I see the sprints × features × stages matrix,
can open a feature trail, filter it, open an evidence snapshot and see the chain/anchor/golden badges.

### US9 — Run it in CI and in workflows (P2)
As a developer, I want the required check to verify the trail on every push and the main branch to anchor
it after a merge, and I want the siblings' workflows to record the people behind the gate verdicts.

---

## 3. Functional requirements

### 3.1 Recording (FR-0xx)

- **FR-001** auditGuard SHALL register a mandatory (`optional: false`) hook on `before_<cmd>` and
  `after_<cmd>` for all ten Spec Kit commands: `specify`, `clarify`, `plan`, `tasks`, `analyze`,
  `checklist`, `constitution`, `converge`, `implement`, `taskstoissues`.
- **FR-002** `hook before_<cmd>` SHALL append `command.started` with: command, stage (FR-101), actor
  (FR-010), git head and dirty flag, the sha256 of every tracked artefact (FR-403), and SHALL store the
  open command in state (`.specify/auditguard/state/open-<feature>.json`).
- **FR-003** `hook after_<cmd>` SHALL append `command.finished` with the fields of FR-002 plus:
  `changed` (artefacts whose hash moved since `command.started`), `duration_s`, `outcome`
  (`pass` | `escalated` | `error` | `unknown`, see FR-005), the gate summaries and evidence collected
  (FR-4xx), and SHALL clear the open state. If no open command exists for `<cmd>`, the event SHALL carry
  `started_missing: true` (the `before_` hook was skipped) and `changed` SHALL be computed against the
  last recorded hashes.
- **FR-004** `hook stop` (Spec Kit runtime event) SHALL close every open command of the project as
  `command.abandoned` with the outcome of FR-005; `hook before_<other>` on a feature with an open command
  SHALL do the same before recording the new start.
- **FR-005** Outcome derivation: an escalation note of scopeGuard (`scopeguard-escalation-*.md`) or
  archiGuard (`gates/escalation-*.md`) created or modified after `command.started` → `escalated`; a
  combined archiGuard verdict `status: error` in the window → `error`; otherwise `pass` for
  `command.finished` and `unknown` for `command.abandoned`.
- **FR-006** `hook session_start` / `session_end` SHALL append `session.started` / `session.ended` to the
  project journal and keep the session id in state; every event appended while a session is open SHALL
  carry `actor.session`.
- **FR-007** Project-level commands (`constitution`) and sprint events SHALL be recorded in the
  `_project` journal of the current sprint with `feature: null`.
- **FR-008** A hook SHALL never change the exit code of the Spec Kit command: in `mode: record` it exits
  `0` in every case and prints one line (`auditGuard <version> | <event> | <sprint> / <feature> | #<seq>
  <kind> (<outcome>) | <n> gate reports, <m> waiver changes | <trail path>`); on an internal failure it
  prints `auditGuard: could not record (<reason>)` and still exits `0`. In `mode: enforce` an internal
  failure exits `2`.
- **FR-009** With `integration: workflow` every hook SHALL print `skipped` and exit `0`; with
  `integration: hooks` the workflow shell step `auditguard hook … --via workflow` SHALL print `skipped`.
  A given event is recorded exactly once.
- **FR-010** Actor resolution: `type: agent` for hook and runtime-event invocations (`id` = `ai` from
  `.specify/init-options.json`, fallback `agent`); `type: human` for `decide`, `note`, `sprint`, `anchor`
  (`id` = `--by`, else `AUDITGUARD_ACTOR`, else `git config user.name`; `role` = `--role`); `type: ci`
  when `CI` is set (`id` from the first set variable in `actors.ci_env`); `type: script` for collector
  events, with the original person kept in `data.by` when the source names one.

### 3.2 Stages and milestones (FR-1xx)

- **FR-101** The stage of a command SHALL come from `stages.<name>.commands` in the config; default:
  `design` = constitution, specify, clarify, plan, tasks, analyze, checklist, taskstoissues;
  `implement` = implement, converge; `test` = none (milestone-only).
- **FR-102** When a `command.started` belongs to a stage the feature has not entered yet, auditGuard SHALL
  first append `stage.entered {stage}`. When it belongs to a stage already completed, it SHALL append
  `stage.reentered {stage, completed_at, completed_by_event}` instead.
- **FR-103** A stage is completed by the first event whose kind is listed in
  `stages.<name>.completed_by`: default `design.signed` for design; `handover.4-5` or
  `implement.approved` for implement; `pr.approved` for test. auditGuard SHALL append
  `stage.completed {stage, by_event}` right after that event.
- **FR-104** Milestone events: `design.signed` (archiGuard `signoff.json` appears, or `decide design
  approve`), `design.reopened` (`signoff.json.reopened` appears, or `decide design reject`),
  `handover.4-5` (`gates/handover-4-5.json` with status green), `implement.approved` (`decide implement
  approve`), `pr.approved` (`decide pr approve`, or a plug-in collector).
- **FR-105** Every event SHALL carry `stage`: the command's stage for command events; the feature's
  current stage (last entered, or `null` before the first) for all other events.

### 3.3 Storage and chain (FR-2xx)

- **FR-201** Layout under `audit.root` (default `audit/`):
  `sprints.yml` · `index.md` · `sprints/<sprint-id>/sprint.md` · `sprints/<sprint-id>/seal.json` ·
  `sprints/<sprint-id>/_project/journal.jsonl` · `sprints/<sprint-id>/<feature-slug>/journal.jsonl` ·
  `…/trail.md` · `…/evidence/<sha256[:12]>-<name>` · `sprints/_unassigned/…` · `features/<slug>.md` ·
  `viewer/index.html` · `viewer/data.js`.
- **FR-202** A journal line is one JSON object (§5.1) with `seq` (1-based per journal), `prev` and
  `hash`. `hash` = SHA-256 of the canonical JSON (sorted keys, `,`/`:` separators, `ensure_ascii=False`)
  of the object without `hash`. `prev` = the previous line's `hash`; for `seq: 1` it is the `hash` of the
  last line of the same feature's journal in the most recent earlier sprint (register order) that has
  one, else 64 zeros. Identical to archiGuard's ledger scheme so one verifier idiom serves both.
- **FR-203** Append SHALL hold an exclusive lock (`journal.jsonl.lock`, `O_EXCL`, 10 s timeout) and SHALL
  refuse (`exit 2`) when the journal fails verification (FR-205) — a broken chain is never extended.
- **FR-204** Journals SHALL be written with LF line endings and UTF-8 regardless of platform, and the
  `.gitattributes` shipped by `configure` SHALL mark `audit/**/*.jsonl` and `audit/**/evidence/**` as
  `-text` (no EOL conversion).
- **FR-205** `verify` (internal) SHALL check for every journal: `seq` contiguous from 1, `prev` links
  (including the cross-sprint link), `hash` recomputes, every evidence reference resolves to a file whose
  sha256 equals `evidence[].sha256`, every `seal.json` matches the heads and counts of its journals and no
  journal in a closed sprint has lines beyond its sealed count, `sprints.yml` is consistent (one open
  sprint at most, ordered, non-overlapping dates). Output: a report (§5.6); exit `1` on any problem.
- **FR-206** Evidence SHALL be stored content-addressed: `evidence/<sha256[:12]>-<basename>`; a file with
  the same sha256 is stored once and referenced many times. Files larger than `evidence.max_file_kb`
  (default 512) SHALL be recorded by `sha256` and `source_path` only, with `snapshot: false`.
- **FR-207** Rendered files (`trail.md`, `sprint.md`, `features/*.md`, `index.md`, `viewer/data.js`) are
  derived: `render` SHALL rebuild them deterministically (byte-identical for the same journals; no
  generation timestamps inside) so that version control shows changes only when the record changed.
- **FR-208** Nothing under `audit/` is ever edited by a person or an agent by hand; every write goes
  through the CLI. The guard (FR-7xx) enforces this for agents; `verify` detects it for everyone.

### 3.4 Sprints, sealing, export (FR-3xx)

- **FR-301** The register `audit/sprints.yml` (§5.3) is the source of truth. The current sprint is the one
  with `status: open` (at most one); if none is open, the sprint whose `start..end` contains today; if
  none matches, `_unassigned`.
- **FR-302** `sprint open <id> [--name --start --end --goal] --by NAME` SHALL add or update the entry,
  set `status: open` (closing nothing automatically — an already open sprint is an error unless
  `--close-current`), record `opened {by, at}` and append `sprint.opened` to the new sprint's `_project`
  journal.
- **FR-303** `sprint close [<id>] --by NAME` SHALL: (1) evaluate the completeness rules (FR-905); in
  `enforce` refuse on a broken rule, in `record` continue and store them as `warnings`; (2) append
  `sprint.closed` to the sprint's `_project` journal; (3) write `seal.json` (§5.4) over every journal and
  evidence file of the sprint; (4) set `status: closed`, `closed {by, at}` in the register; (5) print the
  anchoring commands (`git add audit && git commit … && auditguard anchor --sprint <id>`).
- **FR-304** After a seal, any append targeted at that sprint SHALL be redirected to the current open
  sprint (or `_unassigned`) with `data.redirected_from: <sealed sprint>`; the sealed folder is immutable.
- **FR-305** `export --sprint <id> [--out FILE]` SHALL write `audit-pack-<id>.zip` containing
  `sprints/<id>/**`, `sprints.yml`, `verify-report.json` (internal + golden if available), the viewer
  scoped to the sprint (`viewer/index.html`, `viewer/data.js`), `README.txt` (how to verify offline) and
  `SHA256SUMS`. Zip entries SHALL have fixed timestamps so the same input yields the same archive.
- **FR-306** `verify --pack FILE` SHALL run every internal check (FR-205) on the archive without a
  repository and report the golden checks as `skipped (no repository)`.

### 3.5 Collectors (FR-4xx)

- **FR-401** Collectors run at every hook and on `collect`. Each collector reads sources, compares with
  the last known state reconstructed from the journal (plus a cache in `.specify/auditguard/state/`),
  snapshots new or changed files as evidence and emits events. Re-running with no change SHALL emit
  nothing (idempotence).
- **FR-402** Collector enablement: `true`, `false` or `auto` (= enabled when the sibling extension is
  installed under `.specify/extensions/<id>/`).
- **FR-403 git** — SHALL provide: `commit` (HEAD, short and full), `branch`, `dirty`, `author` of HEAD,
  and the sha256 of every file matching `collectors.git.artefacts` (default `spec.md`, `plan.md`,
  `research.md`, `data-model.md`, `quickstart.md`, `tasks.md`, `handover.yml`, `contracts/**`,
  `checklists/**` under the feature; `.specify/memory/constitution.md` for the project). It SHALL emit
  `commit.merged {commit, author, pr}` for merge commits on `golden.git.base` touching the feature since
  the last recorded one (§3.7 uses them).
- **FR-404 scopeguard** — SHALL run `scopeguard.py report --json --feature-dir <dir>` (read-only) and
  read `<feature>/.scopeguard/history-<gate>.json`, `scopeguard-report.md`,
  `scopeguard-escalation-<gate>.md`, and the `## Scope Coverage` tables of `plan.md` and `tasks.md`.
  It SHALL emit `gate.verdict {tool: scopeguard, gate, status, coverage, pass, violations, waived,
  iterations}` when a gate's history changed, `escalation.raised` for a new or changed escalation note,
  and `waiver.*` (FR-406) for deferral rows (id `defer:<ITEM>`, fields item, reason, location).
- **FR-405 archiguard** — SHALL read `gates/<command>-<a|b|verify>.json`, `gates/<gate>/<check>.json`,
  `gates/escalation-*.md`, `gates/signoff.json` (incl. `reopened`), `gates/handover-4-5.json`,
  `gates/test-loop.json`, `gates/architecture-compliance.md`, `gates/analyze-report.md`,
  `.specify/archiguard/ledger.jsonl`, and the `## Architecture Conformance` rows of `plan.md` marked
  `deviation` or `not applicable`. It SHALL emit `gate.verdict {tool: archiguard, step, status, blocking,
  advisory, waived, iterations, history}` per changed combined verdict, `escalation.raised`,
  `decision {subject: design, verdict: approve, by, role, hashes, pins}` for a new sign-off,
  `design.reopened {by, reason}`, `handover.4-5 {status, commit}`, `test.exit {status}` when
  `test-loop.json` turns green, and `waiver.*` for ledger entries (id = ledger id; owner, approver,
  expires, rules, status) and conformance rows (id `conf:<RULE>`, kind deviation | not-applicable, ADR
  cited).
- **FR-406 waiver history** — For every waiver id the collector SHALL diff the current record against the
  last known one and emit: `waiver.added`, `waiver.changed {before, after}`, `waiver.removed`,
  `waiver.approved`, `waiver.revoked`, `waiver.superseded`, `waiver.expired` (first collection after
  `expires` passed). The event carries the full current record and the evidence (plan rows as text, the
  ledger line).
- **FR-407 workflow** — SHALL read `.specify/workflows/runs/<run_id>/state.json`, `inputs.json` and
  `log.jsonl`, and emit `decision {subject: gate:<step-id>, verdict, run_id, workflow_id}` for every
  `type: gate` step that reached a result since the last collection. The person is `inputs.<verdict
  input>`'s supplier when the workflow records one, else `actor.id` from FR-010 with `confidence:
  inferred`.
- **FR-408 plug-in** — A collector registered under `collectors.<name>.command` is invoked as
  `<command> --feature-dir <dir> --since <iso> --json` with cwd = project root and SHALL print a JSON
  list of `{kind, at, actor, data, evidence: [{name, path}]}`; exit `0` with events, `0` with `[]` for
  nothing, `2` on failure (recorded as `collector.error {name, message}`, never fatal). auditGuard adds
  sprint, feature, stage and chain fields and snapshots the evidence. Kinds SHALL be namespaced
  `<name>.<kind>` unless they are one of the core kinds of §5.2.

### 3.6 Reconciliation — out-of-band changes (FR-5xx)

- **FR-501** At every hook and `collect`, auditGuard SHALL compare the current artefact hashes (FR-403)
  with the last recorded ones for the feature. Every difference not covered by an open command SHALL be
  recorded as `artefact.changed {files: [{path, from, to}], after_signoff, after_handover,
  inferred_command}`.
- **FR-502** `inferred_command` SHALL be derived from the change pattern: `spec.md` only → `specify` or
  `clarify` (`specify|clarify`); `plan.md` with any of `research.md`, `data-model.md`, `quickstart.md`,
  `contracts/**` → `plan`; `tasks.md` only → `tasks`; `checklists/**` → `checklist`; code paths →
  `implement`; otherwise `null`.
- **FR-503** `after_signoff` is true when `gates/signoff.json` exists (and is not re-opened) and a file
  listed in archiGuard's `edit_guard.after_signoff` (or, without archiGuard, `plan.md`, `research.md`,
  `data-model.md`, `quickstart.md`, `contracts/**`) changed; `after_handover` likewise for `spec.md`,
  `handover.yml`, `ba/**` once `handover.yml` exists.
- **FR-504** The rendered trail SHALL mark these events `⚠` and the sprint report SHALL list them under
  *Changes outside recorded commands* with author (from git blame of the committed version when
  available) and inferred command.

### 3.7 Verification against the golden sources, anchoring (FR-6xx)

The golden sources are configured under `golden:` (§7.3). The trail is a set of claims; `verify --golden`
re-derives each claim from its source and labels every event `verified`, `unanchored`, `ephemeral`,
`mismatch` or `unexplained`. Internal verification (FR-205) always runs first.

- **FR-601 G1 commits exist and are reachable.** Every `commit` recorded in an event SHALL exist in the
  repository and be an ancestor of `<golden.git.remote>/<golden.git.base>` or of a branch on that remote
  (`git merge-base --is-ancestor`). A missing or unreachable commit → `mismatch {reason:
  commit_unreachable}` (history was rewritten or never pushed).
- **FR-602 G2 artefact hashes match the golden source.** For each event with `dirty: false`, the sha256 of
  each artefact blob at the recorded commit (`git cat-file blob <commit>:<path>`) SHALL equal the recorded
  hash → `verified`; a difference → `mismatch {path, recorded, golden}`. For `dirty: true`, auditGuard
  SHALL search the later commits of the feature branch and `git.base` for the first commit whose blobs
  equal every recorded hash → `anchored_at: <commit>` and `verified`; if none exists → `ephemeral` (the
  recorded state never reached the golden source — a gated plan that was changed again before commit).
- **FR-603 G3 every golden change is explained.** For every commit reachable from the feature branch since
  `golden.git.base` (and every merge commit into that base touching the feature) that touches
  `golden.git.explain_paths`, there SHALL exist an event in the trail whose window covers the commit's
  files: a `command.*` whose `changed` includes them, an `artefact.changed` listing them, or a
  `commit.merged` naming the commit. Otherwise → `unexplained_commit {commit, author, date, files}`.
- **FR-604 G4 evidence is authentic.** For every evidence snapshot whose source path is committed (for
  example `specs/<f>/gates/plan-b.json`, `.specify/archiguard/ledger.jsonl`), the snapshot's sha256 SHALL
  equal the blob at `anchored_at` (or the recorded commit) → `verified`; else `mismatch`.
- **FR-605 G5 decisions agree with their sources.** `design.signed` → the hashes recorded in the event
  SHALL equal `signoff.json` at its commit and the `spec.md` hash among them SHALL equal the spec hash
  pinned in `handover.yml` (BA golden source) when a handover record exists; `waiver.*` from the ledger →
  the ledger line SHALL exist unchanged (same `hash`) and the ledger SHALL verify (`archiguard ledger
  verify` semantics re-implemented read-only); `gate.verdict` of archiGuard → the `pins.lock` in the
  verdict SHALL equal the sha256 of `standards.lock.yml` at the anchored commit; `decision gate:<step>` →
  the run state SHALL still show that verdict.
- **FR-606 G6 seals are anchored.** For every closed sprint: tag `audit/<sprint-id>` (annotated) SHALL
  exist, point at a commit that contains `audit/sprints/<id>/seal.json` with the same `hash`, and carry
  the seal hash in its message; with `golden.git.sign: true` the tag signature SHALL verify (`git tag -v`).
  A note under `golden.git.notes_ref` on that commit SHALL repeat the seal hash.
- **FR-607 G7 chain heads are anchored.** The latest note under `golden.git.notes_ref` reachable from the
  remote SHALL list journal heads (`{path: {hash, seq}}`); every journal's current head SHALL be the
  listed one or a descendant of it (the listed `seq` ≤ current `seq` and the hash at that `seq` equals the
  listed hash). A journal whose history diverged from an anchored head → `mismatch {reason:
  rewritten_after_anchor}`.
- **FR-608 G8 deterministic evidence is reproducible (optional, `--recompute`).** For a sample or all
  `gate.verdict` events of scopeGuard, auditGuard SHALL check out `anchored_at` into a temporary git
  worktree, run `scopeguard.py report --json`, strip volatile fields (timestamps, version) and compare
  with the snapshot → `verified` or `mismatch {diff}`. archiGuard combined verdicts are compared on their
  `gates[].status` and `findings` sets only (pins and hashes are covered by G5).
- **FR-609 anchor.** `auditguard anchor [--sprint <id>] [--push]` SHALL write a git note on HEAD under
  `golden.git.notes_ref` with `{at, by, journals: {path: {hash, seq}}, seals: {sprint: hash}}`; with
  `--sprint <id>` on a closed sprint it SHALL also create the annotated tag `audit/<id>` (signed when
  `golden.git.sign: true`) on the commit that contains the seal (HEAD must contain it, else exit `2`);
  with `--push` it pushes `refs/notes/auditguard` and the tag to `golden.git.remote`. `anchor` is
  human/CI-only (FR-703).
- **FR-610 output.** `verify --golden` SHALL write `audit/verify-report.json` (§5.6; not chained,
  regenerable) and print a summary: counts per label, then every `mismatch`, `unexplained_commit` and
  `ephemeral` with the exact command to reproduce it (for example `git cat-file blob a1b2c3d:specs/001-x/plan.md | sha256sum`). Exit `1` on any `mismatch` or `unexplained_commit` (both modes — this is
  verification, not recording); `ephemeral` and `unanchored` are informational.
- **FR-611 tracker (plug-in).** When `golden.tracker.command` is set, `verify --golden` SHALL invoke it
  with `--check <json of cited issue keys>` and label `issue.*` events accordingly (v1 ships the contract,
  not a Jira implementation).

### 3.8 Human decisions and the agent guard (FR-7xx)

- **FR-701** `decide <subject> <verdict> --by NAME [--role ROLE] [--reason TEXT] [--feature-dir D]`
  SHALL append `decision {subject, verdict, reason}` with `actor.type: human`. Subjects: `design`
  (verdicts approve → `design.signed` milestone when archiGuard is absent; reject → `design.reopened`),
  `implement` (approve → `implement.approved`), `pr` (approve → `pr.approved`; reject), `escalation:<note
  path or id>` (`accept` | `reject` | `defer`, closes `escalation.raised` → `escalation.decided`),
  `spec-change` (`accept` | `reject`, resolves an `artefact.changed` with `after_handover`), `gate:<step>`
  (records a workflow gate verdict with the person's name when the workflow does not).
- **FR-702** `note "<text>" --by NAME [--feature-dir D]` SHALL append `note` (human, free text, ≤ 4 KB).
- **FR-703** Human-only subcommands: `decide`, `note`, `sprint open`, `sprint close`, `anchor`. The guard
  (FR-704) blocks them for agents; the hook commands' prompts tell the agent never to run them.
- **FR-704** The guard is a `pre_tool_use` runtime event (matcher `Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell`), implemented like archiGuard's edit guard: for edit tools it SHALL block paths
  matching `guard.readonly` (default `audit/**`, `.specify/extensions/auditguard/**`); for shell tools
  it SHALL block a command line that invokes `auditguard` with a human-only subcommand. Exit `2` with a
  one-line message on stderr blocks; any setup problem → exit `0` (fail open). `configure` SHALL print the
  line to add to archiGuard's `edit_guard.always_readonly` when archiGuard is installed.
- **FR-705** Pending items SHALL be derivable from the journal: `escalation.raised` without a later
  `escalation.decided` for the same note hash; `artefact.changed` with `after_handover` without a later
  `decision spec-change`; stages entered but not completed; waivers expiring within
  `render.expiring_days` (default 30). The renderers show them under *Open items*.

### 3.9 Rendering and user interface (FR-8xx)

- **FR-801 Markdown.** `render` SHALL write `trail.md` per sprint-feature, `sprint.md` per sprint,
  `features/<slug>.md` per feature across sprints and `index.md` (§5.7 layouts). With `render.on_hook:
  true` (default) every hook re-renders the affected feature, sprint and index.
- **FR-802 Terminal.** `show [--feature-dir D] [--sprint S] [--stage X] [--kind K] [--actor A] [--open]`
  SHALL print the trail as a table (seq, when, actor, kind, detail) and the *Open items*; `--json`
  prints the events.
- **FR-803 HTML viewer — files.** `render --html` SHALL write `audit/viewer/index.html` (the static
  application, copied from the extension's `templates/viewer/`, unchanged between renders) and
  `audit/viewer/data.js` (`window.AUDITGUARD = {...}`, §5.8). `data.js` SHALL be loaded with a
  `<script src>` tag so the viewer works from `file://` without a server or network. Evidence smaller than
  `viewer.inline_kb` (default 64) is embedded as text; larger evidence is linked by relative path.
- **FR-804 HTML viewer — screens.** The viewer SHALL provide, with hash routes (`#/`, `#/sprint/<id>`,
  `#/feature/<slug>`, `#/feature/<slug>/<sprint>`, `#/event/<hash12>`, `#/decisions`, `#/waivers`,
  `#/verification`):
  1. **Overview** — the sprints × features matrix with a stage pill per cell (`○` not started, `●` in
     progress, `✔` completed with date, `⚠` reentered or out-of-band change), a status bar with the
     verification badges (chain · anchors · golden, each `✔` / `⚠ n` / `–`), and counters: open
     decisions, open escalations, waivers in force / expiring, out-of-band changes.
  2. **Sprint** — seal status and anchor; features table (stage reached, milestone date, gates summary);
     the sprint's decisions, waiver changes, gate verdicts, out-of-band changes; a timeline strip of all
     features in the sprint.
  3. **Feature trail** — a vertical timeline grouped by stage bands (Design / Implement / Test) with the
     stage markers as band headers (entered, completed by whom, reentered); filter chips for stage, kind
     group (command, gate, waiver, decision, escalation, change, session, note), actor type and
     verification label; free-text search; each event expandable to its full record: artefact hashes
     with golden status per file (`✔ anchored at <commit>` / `◌ ephemeral` / `✖ mismatch`), evidence
     list with inline preview (JSON pretty-printed, Markdown rendered by a minimal built-in renderer:
     headings, lists, tables, code), the chain fields, a permalink.
  4. **Decisions** — every `decision` and milestone, filterable by person, role, subject, sprint;
     pending decisions on top.
  5. **Waivers** — waivers in force (by expiry), history per waiver id (added → changed → approved →
     revoked/expired), filter by source (scope deferral, ledger, conformance).
  6. **Evidence** — all snapshots by feature and kind; **compare** two snapshots of the same report (for
     example scopeGuard plan iteration 1 vs 3) with a line diff.
  7. **Verification** — the latest `verify-report.json`: per check G1–G8 the counts and the findings,
     each with its reproduce command (copy button) and a link to the event.
- **FR-805 HTML viewer — constraints.** Vanilla HTML/CSS/JS in one `index.html` (≤ 300 KB), no CDN, no
  web fonts, no external requests; light and dark theme following the OS; print stylesheet (an auditor
  prints a trail); keyboard: `/` focuses search, `Esc` closes a record; responsive to 1024 px; the page
  shows a visible banner when `data.js` is older than the newest journal it lists (`generated` vs the
  max event `at`) and when the verification report is missing or older than the data.
- **FR-806 serve.** `serve [--port 8765] [--open]` SHALL serve `audit/` with Python's `http.server`
  (localhost only) and re-render `data.js` when a journal changed (mtime poll, 2 s); the viewer, when
  loaded over http, polls the `Last-Modified` header of `data.js` every 5 s and reloads the data on change.
- **FR-807 publish.** `docs/ci.md` SHALL include a GitHub Pages job (`render --html` then upload
  `audit/viewer`) and a Bitbucket Pipelines artefact step; `export` includes the viewer scoped to the
  sprint (FR-305).

### 3.10 CLI, configuration, packaging (FR-9xx)

- **FR-901 CLI** (full reference §7.4): `hook`, `collect`, `decide`, `note`, `sprint`, `anchor`,
  `render`, `show`, `verify`, `check`, `export`, `serve`, `configure`, `guard`, `version`. Every command
  accepts `--feature-dir`, `--config`, `--json`, `--verbose`. Feature resolution as in the siblings:
  `SPECIFY_FEATURE_DIRECTORY`, `.specify/feature.json`, branch name, the only directory under `specs/`.
- **FR-902 Exit codes.** `0` ok; `1` `verify` / `check` found problems; `2` cannot run (config error,
  broken chain on append, missing repository for a golden check, HEAD does not contain the seal for
  `anchor`); `3` blocked by policy (a human-only command from an agent context when the guard is
  bypassed and `AUDITGUARD_CONTEXT=agent` is set by the hook commands).
- **FR-903 Config** `.specify/extensions/auditguard/auditguard-config.yml` (§7.3), committed; unknown
  keys are errors; `local-config.yml` may override `integration`, `render.on_hook`, `render.html_on_hook`, `viewer.*`;
  env `AUDITGUARD_ACTOR`, `AUDITGUARD_MODE`, `AUDITGUARD_INTEGRATION`; CI ignores local overrides.
- **FR-904 configure** SHALL set the 20 hooks `enabled` according to `integration`, the 4 events
  according to `guard.enabled` and `sessions.record`, write `.gitattributes` entries (FR-204), create
  `audit/sprints.yml` from the template when missing, and print the table of what is in force
  (integration, mode, hooks on/off, events on/off, collectors detected with versions, golden sources
  reachable, sprint current).
- **FR-905 check** SHALL evaluate the rules of `rules:` (§7.3) for a feature or a sprint and print each
  as `[OK]`, `[WARN]` (`record`) or `[FAIL]` (`enforce`, exit `1`).
- **FR-906 Packaging.** `tools/build.py` SHALL generate the 20 hook command files from one template
  (§7.2), build `dist/auditguard.zip` (extension) and `dist/SHA256SUMS`; `extension.yml` (§7.1) declares
  25 commands, 20 hooks, 4 events, 1 config template; `action.yml` exposes `command: verify | check |
  render | anchor` and `sprint:`; `catalog/extensions.json` for team catalogs; release on tag `vX.Y.Z`.
- **FR-907 No preset.** auditGuard SHALL NOT ship a Spec Kit preset: it changes no template and must not
  wrap commands the siblings' presets already wrap.

---

## 4. Non-functional requirements

- **NFR-001 Dependencies.** Python ≥ 3.9 standard library only; git ≥ 2.20 on PATH for the git collector
  and golden checks (absent git → those features report `skipped`, recording continues).
- **NFR-002 Hook latency.** A hook on a feature with 200 events and the siblings installed completes in
  ≤ 2 s p95 on a developer laptop (collectors read files; the only subprocesses are `git` and one
  `scopeguard.py report`).
- **NFR-003 Never block in record mode.** No code path of a hook raises to the agent; failures are
  printed and swallowed (FR-008).
- **NFR-004 Determinism.** Rendering and export are byte-reproducible for the same inputs (sorted keys,
  fixed zip timestamps, no wall-clock in derived files).
- **NFR-005 Concurrency.** Journal appends are serialised by a lock file; a timed-out lock prints and
  gives up (record) or exits 2 (enforce); no partial line is ever written (write to temp, append in one
  `os.write`, fsync).
- **NFR-006 Portability.** Windows (PowerShell launcher, paths with `!`), macOS, Linux; LF journals
  everywhere; paths in events are POSIX-relative to the project root.
- **NFR-007 Size.** One event ≈ 1–3 KB; evidence deduplicated; a 10-sprint project with 20 features stays
  under 50 MB including evidence at default caps.
- **NFR-008 Privacy.** Recorded identities are what git and the CLI already expose (`user.name`, `--by`,
  CI actor); `sessions.record: false` disables session ids; no prompt or tool-call content is recorded.
- **NFR-009 Fail-open guard, fail-closed verification.** The guard never blocks on its own error; `verify`
  and `check` never report success they did not establish.
- **NFR-010 Compatibility.** Tested against scopeGuard `>=0.4,<0.6` and archiGuard `>=0.1,<0.3`; version
  ranges in `collectors.<id>.version`, an unsupported version is recorded as `collector.error` once and
  the collector disabled for the run.
- **NFR-011 Offline.** `verify --pack`, the viewer and the Markdown views need no network.
- **NFR-012 Accessibility.** Viewer: semantic HTML, keyboard navigable, contrast ≥ 4.5:1 in both themes,
  stage pills carry text labels, not colour alone.

---

## 5. Data contracts

### 5.1 Journal line

```json
{
  "v": 1,
  "seq": 14,
  "at": "2026-10-07T10:42:18+02:00",
  "recorded": "2026-10-07T10:42:19+02:00",
  "sprint": "S-2026-21",
  "feature": "specs/001-place-order",
  "stage": "design",
  "kind": "command.finished",
  "command": "speckit.plan",
  "outcome": "pass",
  "actor": {"type": "agent", "id": "claude", "session": "0b3e4f…", "role": null},
  "source": "hook:after_plan",
  "commit": "a1b2c3d4e5f6…", "branch": "001-place-order", "dirty": true,
  "artefacts": {"spec.md": "9f2e…", "plan.md": "c4d1…", "research.md": "77aa…", "data-model.md": "01bc…", "contracts/orders.yaml": "5e5e…"},
  "changed": ["plan.md", "research.md", "data-model.md", "contracts/orders.yaml"],
  "data": {
    "duration_s": 412,
    "gates": [
      {"tool": "scopeguard", "gate": "plan", "status": "pass", "coverage": "11/11", "waived": 1, "iterations": 2},
      {"tool": "archiguard", "step": "plan-b", "status": "pass", "blocking": 0, "advisory": 1, "waived": 1, "iterations": 2}
    ]
  },
  "evidence": [
    {"name": "scopeguard-report.json", "sha256": "3f9a1c2b7e44…", "path": "evidence/3f9a1c2b7e44-scopeguard-report.json", "source_path": null, "snapshot": true},
    {"name": "plan-b.json", "sha256": "8c01d7aa90f2…", "path": "evidence/8c01d7aa90f2-plan-b.json", "source_path": "specs/001-place-order/gates/plan-b.json", "snapshot": true}
  ],
  "prev": "6d0f…",
  "hash": "e91a…"
}
```

Field rules: `v` schema version; `at` when it happened (source timestamp when the source has one),
`recorded` present only when it differs from `at`; `feature` null for project events; `stage` per FR-105;
`actor.type` ∈ human | agent | ci | script; `source` ∈ `hook:<event>` | `event:<name>` |
`collector:<name>` | `cli` | `ci`; `commit` full sha or null outside git; `artefacts` and `changed`
present on command and `artefact.changed` events; `data` is kind-specific (§5.2); `evidence[].path`
relative to the journal's folder; `prev`/`hash` per FR-202. Verification labels are **not** stored in
the journal (they are claims about the journal) — they live in `verify-report.json` and in `data.js`.

### 5.2 Event kinds and their `data`

| Kind | `data` |
|------|--------|
| `sprint.opened`, `sprint.closed` | `{id, name, start, end, goal, warnings[]}` |
| `session.started`, `session.ended` | `{agent, integration, session}` |
| `command.started` | `{args_sha256}` |
| `command.finished`, `command.abandoned` | `{duration_s, gates[], started_missing?, outcome_reason?}` |
| `stage.entered`, `stage.reentered`, `stage.completed` | `{stage, by_event?, completed_at?, completed_by_event?}` |
| `gate.verdict` | `{tool, gate|step, status, coverage?, blocking?, advisory?, waived?, violations?, iterations, history[]}` |
| `escalation.raised`, `escalation.decided` | `{note, note_sha256, tool, items[], verdict?, reason?}` |
| `waiver.*` | `{id, source: scope|ledger|conformance, item|rule(s), reason, owner?, approver?, expires?, status, before?, after?}` |
| `decision` | `{subject, verdict, reason?, run_id?, workflow_id?, hashes?, pins?, confidence?}` |
| `design.signed`, `design.reopened`, `handover.4-5`, `implement.approved`, `test.exit`, `pr.approved` | `{by?, role?, reason?, status?, hashes?, pins?}` |
| `artefact.changed` | `{files: [{path, from, to}], after_signoff, after_handover, inferred_command, author?}` |
| `commit.merged` | `{commit, author, date, pr?, files[]}` |
| `note` | `{text}` |
| `ci.verified` | `{internal: pass|fail, golden: pass|fail|skipped, report_sha256}` |
| `collector.error` | `{name, message}` |

### 5.3 Sprint register `audit/sprints.yml`

```yaml
version: 1
sprints:
  - id: S-2026-21              # ^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$ ; becomes the folder name
    name: "Sprint 21"
    start: 2026-10-06
    end: 2026-10-17
    goal: "Orders: place order end to end"
    status: closed             # planned | open | closed
    opened: { by: "Roman", at: "2026-10-06T08:30:00+02:00" }
    closed: { by: "Roman", at: "2026-10-17T17:05:00+02:00", seal: "f0a3…" }
  - id: S-2026-22
    name: "Sprint 22"
    start: 2026-10-20
    end: 2026-10-31
    status: open
```

Constraints: ids unique; at most one `open`; `start ≤ end`; ranges do not overlap; order in the file is
chronological (used for the cross-sprint chain link).

### 5.4 `seal.json`

```json
{"v": 1, "sprint": "S-2026-21", "closed_by": "Roman", "at": "2026-10-17T17:05:00+02:00",
 "journals": {"_project/journal.jsonl": {"head": "9a…", "count": 6},
              "001-place-order/journal.jsonl": {"head": "e9…", "count": 41}},
 "evidence": {"count": 37, "sha256_of_sorted_hashes": "c1…"},
 "warnings": [{"rule": "escalations_decided_before_close", "items": ["001-place-order: escalation-tasks-b.md"]}],
 "hash": "f0a3…"}
```

`hash` = SHA-256 of the canonical JSON without `hash`.

### 5.5 Anchor note (git note under `refs/notes/auditguard`)

```json
{"v": 1, "at": "2026-10-17T17:20:00+02:00", "by": "Roman",
 "journals": {"audit/sprints/S-2026-21/001-place-order/journal.jsonl": {"hash": "e9…", "seq": 41}},
 "seals": {"S-2026-21": "f0a3…"}}
```

Tag `audit/S-2026-21`: annotated, message `auditGuard seal S-2026-21 f0a3…`, signed when configured.

### 5.6 `verify-report.json`

```json
{"v": 1, "generated": "…", "auditguard": "0.1.0", "scope": {"sprints": ["all"]},
 "internal": {"status": "pass", "journals": 7, "events": 212, "evidence": 143, "problems": []},
 "golden": {"status": "fail", "sources": {"git": "ok", "handover": "ok", "lock": "ok", "ledger": "ok", "workflow": "ok", "tracker": "not configured"},
   "checks": {"G1": {"verified": 212, "mismatch": 0}, "G2": {"verified": 180, "ephemeral": 9, "mismatch": 1}, "G3": {"explained": 54, "unexplained": 1}, "G4": {...}, "G5": {...}, "G6": {...}, "G7": {...}, "G8": {"skipped": "not requested"}},
   "events": {"e91a…": {"label": "verified", "anchored_at": "b2c3…"}, "77ff…": {"label": "ephemeral"}},
   "findings": [
     {"check": "G3", "label": "unexplained_commit", "commit": "d4e5…", "author": "J. Doe", "date": "2026-10-09", "files": ["specs/001-place-order/plan.md"],
      "reproduce": "git show --stat d4e5…"},
     {"check": "G2", "label": "mismatch", "event": "a0b1…", "path": "specs/001-place-order/plan.md", "recorded": "c4d1…", "golden": "aa00…",
      "reproduce": "git cat-file blob a1b2c3d:specs/001-place-order/plan.md | sha256sum"}
   ]}}
```

### 5.7 Markdown layouts

`trail.md` — header block (feature, sprint, stage line `design ✔ signed <date> by <who> · implement ●
in progress · test ○`, open items line), then one section per stage with a table `# | When | Actor |
Event | Detail | Evidence`, then *Decisions (human in the loop)*, *Waivers and deferrals*, *Gate
verdicts*, *Changes outside recorded commands*, *Chain* (first/last hash, count, seal/anchor status if
known from the last verify). Rows for `artefact.changed` and `stage.reentered` are prefixed `⚠`.

`sprint.md` — seal and anchor status; *Features and stages* (feature | design | implement | test |
milestones); the same five lists across features; *Sessions*.

`features/<slug>.md` — the feature across sprints: per sprint a link to its `trail.md` and the stage line.

`index.md` — matrix sprints × features × stages with `○ ● ✔ ⚠`, counters, last verification summary.

### 5.8 Viewer data bundle `data.js`

```js
window.AUDITGUARD = {
  v: 1, generated: "…", project: {name, root, git_base},
  register: {...sprints.yml...},
  features: [{slug, path, sprints: [...], stages: {design: {status, at, by}, implement: {...}, test: {...}}}],
  events: [ ...journal lines, plus {_sprint_dir, _journal} ],          // scoped to the export when packed
  waivers: [{id, source, current: {...}, history: [eventHash...]}],
  decisions: [eventHash...], open_items: {decisions: [...], escalations: [...], expiring: [...]},
  verification: {...verify-report.json or null...},
  evidence: {"3f9a1c2b7e44…": {name, path, size, inline: "…text…" | null}}
};
```

---

## 6. Behaviour

### 6.1 Hook state machine (per feature)

```
                 before_<X>                       after_<X>
   IDLE ───────────────────────► OPEN(X) ─────────────────────────► IDLE
    ▲   reconcile → artefact.changed*            changed, outcome, collectors,
    │   collectors*; stage.entered?              command.finished; milestones?
    │   command.started
    │
    │   stop / before_<Y≠X> / before_<X> again
    └───────────── OPEN(X) → command.abandoned(outcome FR-005) → continue as IDLE
```

Every transition: resolve feature → resolve sprint (FR-301; redirect if sealed, FR-304) → load journal
tail and verify the last 2 lines (cheap tamper check; full verify is `verify`) → run → append → render
(if `render.on_hook`).

### 6.2 Waiver diff

`state[id] = {source, item|rules, reason, owner, approver, expires, status, evidence_sha}` reconstructed
from the last `waiver.*` event per id in the feature's journals (all sprints) and cached. Current set
from the three sources. Emit per id: new → `added`; gone → `removed`; `status` approved/revoked/superseded
transitions → that kind; any other field change → `changed {before, after}`; `expires < today` and no
`expired` yet → `expired`. Sort emitted events by id for determinism.

### 6.3 Reconciliation (FR-5xx)

`last = artefacts of the last event carrying artefacts` ∪ `{}`; `now = hashes now`. `diff = {p: (last[p],
now[p])}` for changed, added (`from: null`) and deleted (`to: null`). If an open command exists, the diff
belongs to it (no event). Else emit one `artefact.changed` with flags (FR-503), `inferred_command`
(FR-502) and, when `git log -1 --format=%an -- <path>` shows the change is committed, `author`.

### 6.4 Golden verification order

internal (FR-205) → load register and all journals → G1 (one `git cat-file --batch-check` and one
`merge-base` per distinct commit) → G2 (one `git cat-file --batch` pass per distinct commit; for
`dirty` events, search commits in `git log --format=%H -- <feature>` after the event's commit) → G3
(`git log --name-only <base>..<branch>` plus merges into base) → G4/G5 (blob reads at `anchored_at`) →
G6/G7 (`git tag -l 'audit/*'`, `git notes --ref … list`, `git tag -v` when signed) → G8 (optional
worktree). Each check writes its findings; labels per event are the worst label of its checks.

### 6.5 Sprint close and anchor (happy path)

```bash
A=.specify/extensions/auditguard/scripts/bash/auditguard.sh
bash $A check --sprint S-2026-21                 # rules: [OK]/[WARN]
bash $A sprint close S-2026-21 --by "Roman"      # sprint.closed, seal.json, register closed
git add audit && git commit -m "Close sprint S-2026-21 (seal f0a3…)"
bash $A anchor --sprint S-2026-21 --push         # note on HEAD + tag audit/S-2026-21 (+ -s), pushed
bash $A export --sprint S-2026-21                # audit-pack-S-2026-21.zip
bash $A sprint open S-2026-22 --name "Sprint 22" --start 2026-10-20 --end 2026-10-31 --by "Roman"
```

---

## 7. Interfaces

### 7.1 `extension.yml`

```yaml
schema_version: "1.0"
extension:
  id: auditguard
  name: "auditGuard"
  version: "0.1.0"
  description: "Tamper-evident audit trail of the Spec Kit SDLC: every command by stage, gate reports of scopeGuard and archiGuard, waiver history, human decisions and out-of-band changes, per sprint and feature, verifiable against git and the other golden sources"
  author: "rlgdev"
  repository: https://github.com/rlgdev/spec-kit-auditguard
  homepage: https://github.com/rlgdev/spec-kit-auditguard
  license: MIT
  category: process
  effect: read-write          # writes only under audit/ and .specify/auditguard/state/
requires:
  speckit_version: ">=1.0.1"  # hooks on all commands + agent runtime events
  tools:
    - { name: python, version: ">=3.9", required: false, description: "Runs the engine (standard library only); launchers fall back to specify-cli's Python or uv" }
    - { name: git, version: ">=2.20", required: false, description: "Golden-source verification and anchoring; recording works without it" }
provides:
  commands:
    # 20 generated hook commands: <cmd>entry / <cmd>exit for specify, clarify, plan, tasks, analyze, checklist, constitution, converge, implement, taskstoissues
    - { name: speckit.auditguard.specifyentry,      file: commands/speckit.auditguard.specifyentry.md,      description: "(hook) record the start of /speckit.specify" }
    - { name: speckit.auditguard.specifyexit,       file: commands/speckit.auditguard.specifyexit.md,       description: "(hook) record the end of /speckit.specify: changes, outcome, gate reports" }
    - { name: speckit.auditguard.clarifyentry,      file: commands/speckit.auditguard.clarifyentry.md,      description: "(hook) record the start of /speckit.clarify" }
    - { name: speckit.auditguard.clarifyexit,       file: commands/speckit.auditguard.clarifyexit.md,       description: "(hook) record the end of /speckit.clarify" }
    - { name: speckit.auditguard.planentry,         file: commands/speckit.auditguard.planentry.md,         description: "(hook) record the start of /speckit.plan" }
    - { name: speckit.auditguard.planexit,          file: commands/speckit.auditguard.planexit.md,          description: "(hook) record the end of /speckit.plan: changes, outcome, gate reports, waiver changes" }
    - { name: speckit.auditguard.tasksentry,        file: commands/speckit.auditguard.tasksentry.md,        description: "(hook) record the start of /speckit.tasks" }
    - { name: speckit.auditguard.tasksexit,         file: commands/speckit.auditguard.tasksexit.md,         description: "(hook) record the end of /speckit.tasks" }
    - { name: speckit.auditguard.analyzeentry,      file: commands/speckit.auditguard.analyzeentry.md,      description: "(hook) record the start of /speckit.analyze" }
    - { name: speckit.auditguard.analyzeexit,       file: commands/speckit.auditguard.analyzeexit.md,       description: "(hook) record the end of /speckit.analyze" }
    - { name: speckit.auditguard.checklistentry,    file: commands/speckit.auditguard.checklistentry.md,    description: "(hook) record the start of /speckit.checklist" }
    - { name: speckit.auditguard.checklistexit,     file: commands/speckit.auditguard.checklistexit.md,     description: "(hook) record the end of /speckit.checklist" }
    - { name: speckit.auditguard.constitutionentry, file: commands/speckit.auditguard.constitutionentry.md, description: "(hook) record the start of /speckit.constitution (project journal)" }
    - { name: speckit.auditguard.constitutionexit,  file: commands/speckit.auditguard.constitutionexit.md,  description: "(hook) record the end of /speckit.constitution" }
    - { name: speckit.auditguard.convergeentry,     file: commands/speckit.auditguard.convergeentry.md,     description: "(hook) record the start of /speckit.converge" }
    - { name: speckit.auditguard.convergeexit,      file: commands/speckit.auditguard.convergeexit.md,      description: "(hook) record the end of /speckit.converge" }
    - { name: speckit.auditguard.implemententry,    file: commands/speckit.auditguard.implemententry.md,    description: "(hook) record the start of /speckit.implement (enters the implement stage)" }
    - { name: speckit.auditguard.implementexit,     file: commands/speckit.auditguard.implementexit.md,     description: "(hook) record the end of /speckit.implement" }
    - { name: speckit.auditguard.taskstoissuesentry, file: commands/speckit.auditguard.taskstoissuesentry.md, description: "(hook) record the start of /speckit.taskstoissues" }
    - { name: speckit.auditguard.taskstoissuesexit, file: commands/speckit.auditguard.taskstoissuesexit.md, description: "(hook) record the end of /speckit.taskstoissues" }
    - { name: speckit.auditguard.trail,     file: commands/speckit.auditguard.trail.md,     description: "Render and show the current feature's audit trail for this sprint, with open items" }
    - { name: speckit.auditguard.collect,   file: commands/speckit.auditguard.collect.md,   description: "Collect new evidence of scopeGuard, archiGuard and workflow runs into the trail now" }
    - { name: speckit.auditguard.verify,    file: commands/speckit.auditguard.verify.md,    description: "Verify every chain, seal and evidence hash, and the trail against git and the other golden sources" }
    - { name: speckit.auditguard.configure, file: commands/speckit.auditguard.configure.md, description: "Apply auditguard-config.yml to Spec Kit's hooks and events and show what is in force" }
    - { name: speckit.auditguard.guard,     file: commands/speckit.auditguard.guard.md,     description: "(agent event) record session start/end, close abandoned commands on stop, and block agent edits under audit/ and the human-only auditGuard commands" }
  config:
    - { name: "auditguard-config.yml", template: "config-template.yml", description: "auditGuard settings: integration, mode, audit folder, stages, collectors, golden sources, rules, guard, viewer", required: false }
hooks:
  before_specify:      { command: speckit.auditguard.specifyentry,       optional: false, description: "auditGuard: record start" }
  after_specify:       { command: speckit.auditguard.specifyexit,        optional: false, description: "auditGuard: record end" }
  before_clarify:      { command: speckit.auditguard.clarifyentry,       optional: false, description: "auditGuard: record start" }
  after_clarify:       { command: speckit.auditguard.clarifyexit,        optional: false, description: "auditGuard: record end" }
  before_plan:         { command: speckit.auditguard.planentry,          optional: false, description: "auditGuard: record start" }
  after_plan:          { command: speckit.auditguard.planexit,           optional: false, description: "auditGuard: record end" }
  before_tasks:        { command: speckit.auditguard.tasksentry,         optional: false, description: "auditGuard: record start" }
  after_tasks:         { command: speckit.auditguard.tasksexit,          optional: false, description: "auditGuard: record end" }
  before_analyze:      { command: speckit.auditguard.analyzeentry,       optional: false, description: "auditGuard: record start" }
  after_analyze:       { command: speckit.auditguard.analyzeexit,        optional: false, description: "auditGuard: record end" }
  before_checklist:    { command: speckit.auditguard.checklistentry,     optional: false, description: "auditGuard: record start" }
  after_checklist:     { command: speckit.auditguard.checklistexit,      optional: false, description: "auditGuard: record end" }
  before_constitution: { command: speckit.auditguard.constitutionentry,  optional: false, description: "auditGuard: record start" }
  after_constitution:  { command: speckit.auditguard.constitutionexit,   optional: false, description: "auditGuard: record end" }
  before_converge:     { command: speckit.auditguard.convergeentry,      optional: false, description: "auditGuard: record start" }
  after_converge:      { command: speckit.auditguard.convergeexit,       optional: false, description: "auditGuard: record end" }
  before_implement:    { command: speckit.auditguard.implemententry,     optional: false, description: "auditGuard: record start" }
  after_implement:     { command: speckit.auditguard.implementexit,      optional: false, description: "auditGuard: record end" }
  before_taskstoissues: { command: speckit.auditguard.taskstoissuesentry, optional: false, description: "auditGuard: record start" }
  after_taskstoissues: { command: speckit.auditguard.taskstoissuesexit,  optional: false, description: "auditGuard: record end" }
events:
  session_start: { command: speckit.auditguard.guard, timeout: 10 }      # guard dispatches on hook_event_name: records session.started
  stop:          { command: speckit.auditguard.guard, timeout: 20 }      # closes abandoned commands
  session_end:   { command: speckit.auditguard.guard, timeout: 10 }
  pre_tool_use:  { command: speckit.auditguard.guard, matcher: "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell", timeout: 10 }
tags: ["audit", "traceability", "governance", "compliance", "human-in-the-loop"]
```

The `guard` command's script reads the event payload on stdin and dispatches on `hook_event_name`
(`SessionStart` → `hook session_start`, `Stop` → `hook stop`, `SessionEnd` → `hook session_end`,
`PreToolUse` → guard), so one event command serves all four.

### 7.2 Hook command template (generates the 20 files)

```markdown
---
description: "auditGuard: record the {START_OR_END} of /speckit.{CMD} in the audit trail (hook {EVENT})"
scripts:
  sh: bash scripts/bash/auditguard.sh hook {EVENT} --via hooks
  ps: scripts/powershell/auditguard.ps1 hook {EVENT} --via hooks
  py: scripts/python/auditguard.py hook {EVENT} --via hooks
---

## Goal

Append the `{EVENT}` record of this feature to the audit trail. auditGuard is a recorder: it decides
nothing, repairs nothing and never changes the result of /speckit.{CMD}.

## Steps

1. Run `{SCRIPT}` from the repository root. If the Setup step of /speckit.{CMD} gave you a FEATURE_DIR,
   append `--feature-dir <FEATURE_DIR>`. Set the environment variable `AUDITGUARD_CONTEXT=agent`.
2. Show its one-line output as is. If it prints `skipped`, auditGuard records through workflow steps in
   this project; continue.
3. Continue with /speckit.{CMD}. Never edit anything under `audit/` or `.specify/auditguard/`, and never
   run `auditguard decide`, `note`, `sprint` or `anchor`: those record decisions of people.
4. If the script fails, show its message and continue: the trail is reconciled at the next hook.
```

### 7.3 `config-template.yml`

```yaml
# auditGuard configuration
# Location: .specify/extensions/auditguard/auditguard-config.yml (committed; change through a pull request)
# Workstation overrides: local-config.yml (integration, render.on_hook, render.html_on_hook, viewer.*). CI reads only this file.
# After changing integration, guard or sessions: bash .specify/extensions/auditguard/scripts/bash/auditguard.sh configure
version: 1

integration: hooks            # hooks (default) | workflow (hooks print skipped; workflow shell steps and CI record)
mode: record                  # record = never blocks | enforce = check fails on a broken rule, hook internals exit 2

audit:
  root: audit
  register: audit/sprints.yml
  unassigned: _unassigned     # folder for events outside any sprint
  project: _project           # folder for project-level events (constitution, sprint open/close, sessions, notes)
  evidence:
    snapshot: true
    max_file_kb: 512          # larger: sha256 and source path only

render:
  on_hook: true               # rebuild trail.md / sprint.md / features/*.md / index.md after every hook
  html_on_hook: false         # also rebuild viewer/data.js on every hook (true for teams that keep the viewer open)
  expiring_days: 30           # waivers expiring within this window are open items

viewer:
  inline_kb: 64               # evidence up to this size is embedded in data.js
  theme: auto                 # auto | light | dark

sessions:
  record: true                # session.started / session.ended and actor.session on events

stages:
  design:    { commands: [constitution, specify, clarify, plan, tasks, analyze, checklist, taskstoissues], completed_by: [design.signed] }
  implement: { commands: [implement, converge], completed_by: [handover.4-5, implement.approved] }
  test:      { commands: [], completed_by: [pr.approved] }

collectors:
  git:        { enabled: true, artefacts: [spec.md, plan.md, research.md, data-model.md, quickstart.md, tasks.md, handover.yml, "contracts/**", "checklists/**"], code: ["src/**", "app/**", "lib/**", "tests/**"] }
  scopeguard: { enabled: auto, version: ">=0.4,<0.6", report: true }
  archiguard: { enabled: auto, version: ">=0.1,<0.3", ledger: .specify/archiguard/ledger.jsonl }
  workflow:   { enabled: true, runs: .specify/workflows/runs }
  # jira:     { command: tools/jira-collector.py, timeout: 60 }      # plug-in (FR-408)

golden:
  git:
    enabled: true
    remote: origin
    base: main                # commits must be reachable from remote/base or a remote branch
    explain_paths: ["specs/**", "src/**", "app/**", "lib/**", "tests/**", ".specify/memory/**", "audit/**"]
    notes_ref: refs/notes/auditguard
    sign: false               # true: annotated tags audit/<sprint> are signed (git tag -s) and verified (git tag -v)
  handover: handover.yml      # BA specification pins (archiGuard handover record) checked by G5
  lock: .specify/archiguard/standards.lock.yml
  ledger: .specify/archiguard/ledger.jsonl
  tracker: null               # { command: tools/jira-golden.py } — issue keys cited in events are checked (FR-611)

actors:
  human: auto                 # auto = git user.name; --by and AUDITGUARD_ACTOR override
  agent: auto                 # auto = ai from .specify/init-options.json
  ci_env: [GITHUB_ACTOR, BITBUCKET_STEP_TRIGGERER_UUID, GITLAB_USER_LOGIN]

rules:                        # evaluated by `check` and at `sprint close`: [WARN] in record, [FAIL] in enforce
  implement_requires_design_signed: true
  waiver_requires_approver: true
  escalations_decided_before_close: true
  no_changes_after_signoff: true
  commands_closed_before_close: true
  no_unassigned_events: true
  anchored_before_export: false     # export refuses when the sprint's seal is not anchored

guard:
  enabled: true
  readonly: ["audit/**", ".specify/extensions/auditguard/**"]
  human_only: [decide, note, "sprint open", "sprint close", anchor]
```

### 7.4 CLI reference

```text
auditguard hook <before_X|after_X|stop|session_start|session_end> [--feature-dir D] [--via hooks|workflow]
auditguard collect [--feature-dir D | --all] [--since ISO]
auditguard decide <design|implement|pr|escalation:<ref>|spec-change|gate:<step>> <approve|reject|accept|defer> --by NAME [--role ROLE] [--reason TEXT] [--feature-dir D]
auditguard note "<text>" --by NAME [--feature-dir D]
auditguard sprint list | current | open <id> [--name N --start D --end D --goal G] [--close-current] --by NAME | close [<id>] --by NAME
auditguard anchor [--sprint <id>] [--push] [--by NAME]
auditguard render [--sprint S] [--feature-dir D] [--all] [--html]
auditguard show [--feature-dir D] [--sprint S] [--stage X] [--kind K] [--actor A] [--open] [--json]
auditguard verify [--sprint S | --all] [--golden] [--recompute] [--pack FILE] [--record] [--json]
auditguard check [--feature-dir D] [--sprint S] [--json]
auditguard export --sprint S [--out FILE]
auditguard serve [--port 8765] [--open]
auditguard configure [--dry-run]
auditguard guard                      # event payload on stdin
auditguard version
```

Launchers: `scripts/bash/auditguard.sh`, `scripts/powershell/auditguard.ps1`, `scripts/python/auditguard.py`
(same Python discovery as the siblings: `python`, then specify-cli's interpreter, then `uv run`).

### 7.5 GitHub Action `action.yml`

Inputs `command` (`verify` default | `check` | `render` | `anchor`), `sprint` (`all`), `golden`
(`"true"`), `recompute` (`"false"`), `push` (`"false"`, for `anchor`). Writes the verify summary to the
job summary, uploads `audit/viewer` as an artefact when `command: render`. Recommended wiring in
`docs/ci.md`: pull requests → `verify --golden`; push to main → `anchor --push` then `verify --golden
--record`.

---

## 8. User interface specification (the viewer)

### 8.1 Overview screen

```
┌ auditGuard · acme/orders ───────────────────────────────── chain ✔ · anchors ✔ · golden ⚠ 2 ─┐
│ Open decisions 1 · Open escalations 0 · Waivers in force 4 (1 expiring ≤30 d) · OOB changes 2 │
├───────────────────────────────────────────────────────────────────────────────────────────────┤
│ Feature            │ S-2026-20 (closed ✔) │ S-2026-21 (closed ✔) │ S-2026-22 (open)         │
│ 001-place-order    │ design ●             │ design ✔ · impl ●    │ impl ✔ · test ●          │
│ 002-refunds        │ –                    │ design ● ⚠           │ design ✔ · impl ●        │
│ 003-invoicing      │ –                    │ –                    │ design ●                 │
│ _project           │ constitution         │ sprint open/close    │ sprint open              │
├───────────────────────────────────────────────────────────────────────────────────────────────┤
│ Search /   Filters: stage ▾ kind ▾ actor ▾ label ▾        Decisions · Waivers · Verification │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

Clicking a cell opens the feature trail for that sprint; clicking a sprint header opens the sprint
screen; the badges open the verification screen.

### 8.2 Feature trail screen

```
001-place-order · S-2026-21            ◄ S-2026-20   S-2026-22 ►         ⬇ trail.md  ⎘ permalink
design ✔ signed 2026-10-08 by Tech lead · implement ● in progress · test ○
chips: [all] [command] [gate] [waiver] [decision] [escalation] [change ⚠] [session] [note] · actor: [agent] [human] [ci] · label: [verified] [ephemeral] [mismatch]
─────────────────────────────────────────────── DESIGN · entered 10-06 09:12 · completed 10-08 14:00 (design.signed) ───
 #3   10-06 09:12  agent claude     specify  started → finished (pass)         spec.md +            ✔ anchored a1b2c3d
 #7   10-06 11:40  agent claude     plan     started → finished (pass)         4 files · scope 11/11 (2 it.) · A3 PASS   ✔
 #8   10-06 11:40  script           waiver.added  FR-007 deferred — "PO decision 2026-10-05: phase 2"             ✔
 #12  10-07 09:05  agent claude     tasks    started → abandoned (escalated)   escalation-tasks-b.md                ✔
 #13  10-07 10:30  human Roman · lead architect   escalation decided: accept   "US3 → sprint 22, RFI-14"           ✔
 #15  10-08 14:00  human Tech lead  ★ design.signed   6 artefacts · evidence green                                  ✔
─────────────────────────────────────────────── IMPLEMENT · entered 10-09 08:50 ───────────────────────────────────
 #16  10-09 08:50  agent claude     implement started     A4.1 entry pin ✔                                          ◌ ephemeral
 #19  10-09 13:22  script           ⚠ artefact.changed    plan.md outside a command · after sign-off · J. Doe        ✖ unexplained commit d4e5…
 ...
▼ #19 expanded: files [{plan.md c4d1… → aa00…}] · inferred: plan · author J. Doe (commit d4e5…, 10-09 13:20)
   reproduce: git show --stat d4e5…   [copy]       chain: prev 6d0f… hash 77ff…      evidence: —
```

Interactions: click a row to expand; evidence names open a side panel (JSON pretty, Markdown rendered,
text raw; `compare with…` for two snapshots of the same report name); `★` marks milestones; the stage
band header shows reentries (`↺ reentered 10-11 by plan`); `⎘ permalink` copies `#/event/<hash12>`.

### 8.3 Other screens

**Sprint** — seal card (closed by, at, seal hash, anchor tag ✔/–), features × stages table, timeline
strip (one lane per feature, dots coloured by kind group, hover shows the event), then the five lists
(decisions, waiver changes, gate verdicts, out-of-band changes, sessions). **Decisions** — table with
person, role, subject, verdict, reason, sprint, feature, evidence; *Pending* group on top. **Waivers** —
*In force* (sorted by expiry, red under 30 days) and *History* (one row per `waiver.*` event; expanding
an id shows its chain of changes). **Evidence** — grouped by feature and report name; version list with
compare. **Verification** — report header (when, scope, status), check cards G1–G8 with counts, findings
table with reproduce commands.

### 8.4 Implementation notes

One `index.html` with embedded CSS and JS (~1500 lines), ES2018, no build step; a hash router; data from
`window.AUDITGUARD`; a 150-line Markdown renderer (headings, paragraphs, lists, tables, fenced code,
inline code, bold, links); an LCS line diff for *compare*; CSS variables for the two themes; `@media
print` hides chrome and expands the current trail. Golden labels per event come from
`verification.events[hash].label`; absent report → neutral badge "not verified".

---

## 9. Repository, modules, tests, release

### 9.1 Layout

```text
spec-kit-auditguard/
  extension.yml  config-template.yml  README.md  CHANGELOG.md  LICENSE  action.yml  .extensionignore  .gitattributes
  commands/                            25 files; the 20 hook commands are generated (tools/build.py) from templates/hook-command.md
  scripts/bash/auditguard.sh  scripts/powershell/auditguard.ps1  scripts/python/auditguard.py
  scripts/python/auditguard_core/
    __init__.py (version)  cli.py  config.py  configure.py  common.py (sha256, canonical json, git helpers, feature resolution)  yamlio.py
    journal.py      append/load/verify, cross-sprint prev, lock
    evidence.py     content-addressed store
    sprints.py      register, current sprint, redirect, seal
    stages.py       command→stage, entered/reentered/completed
    hooks.py        state machine (§6.1), outcome, open-command state
    reconcile.py    out-of-band detection (§6.3)
    render_md.py    trail/sprint/features/index
    render_html.py  data.js bundle + copy of templates/viewer/index.html
    serve.py        http.server + re-render on change
    verify.py       internal checks; report
    golden.py       G1–G8; anchor; notes/tags
    check.py        rules
    export.py       audit pack; verify --pack
    guard.py        PreToolUse guard + runtime event dispatch
    collectors/     base.py  git.py  scopeguard.py  archiguard.py  workflow.py  plugged.py
  templates/  hook-command.md  sprints-template.yml  md/*.md (section templates)  viewer/index.html  export-README.txt
  docs/  events.md  collectors.md  golden.md  viewer.md  configuration.md  ci.md  workflows.md
  examples/orders/   archiGuard's example project + two sprints of audit/ (one escalation, one sign-off, one OOB change, one sealed sprint, anchor notes in a bundled .git)
  tests/  unit per module · golden files for render (fixtures → expected md and data.js) · collectors against vendored sibling fixtures · golden verification against a temp git repo built in the test · guard payloads · export round-trip (export → verify --pack) · viewer smoke (Playwright optional job: open file://, assert matrix and a trail render)
  tools/build.py (commands, dist/auditguard.zip, dist/SHA256SUMS)  tools/e2e-speckit.sh (specify init + siblings + auditGuard, hooks driven from the CLI, asserts trail and verify)
  catalog/extensions.json
  .github/workflows/ci.yml  release.yml  pages.yml (viewer demo from examples/orders)
```

### 9.2 Implementation order (dependency-ordered)

1. `common`, `config`, `journal`, `evidence`, `sprints` (register, current, redirect), `stages`,
   `hooks` with the git collector, `render_md`, `show`, `configure`, hook command generation, launchers.
   → US1, US2, US5 (reconciliation), Markdown views.
2. Collectors `scopeguard`, `archiguard`, `workflow`, waiver diff, `decide`/`note`, guard. → US3, US4.
3. `seal`, `export`, `verify` internal and `--pack`, `check` rules. → US6.
4. `golden` G1–G7, `anchor`, `--record`; G8 `--recompute`. → US7.
5. `render_html` + viewer, `serve`, Pages job. → US8.
6. Action, docs, examples, e2e, CHANGELOG, release 0.1.0. → US9.

### 9.3 Release conventions

Same as the siblings: version in `extension.yml`, `auditguard_core/__init__.py`, `catalog/*.json`;
CHANGELOG entry; tag `vX.Y.Z`; the release workflow runs the tests, builds `dist/`, attaches
`auditguard.zip` and `SHA256SUMS`.

---

## 10. Success criteria

- **SC-001** In the e2e run (specify → plan → tasks → implement with hooks), every command has exactly one
  `command.started` and one `command.finished` or `command.abandoned`; no event is duplicated when the
  same hook is invoked twice.
- **SC-002** Editing any byte of any journal line, deleting a line, deleting a sprint folder, or editing
  an evidence file is detected by `verify` (100 % of the mutation tests).
- **SC-003** A commit that changes `plan.md` without a recorded command is reported by
  `verify --golden` as `unexplained_commit` with the right author and file; a recorded dirty state that
  was later committed is labelled `verified` with the right `anchored_at`; one that was never committed
  is `ephemeral`.
- **SC-004** `export` then `verify --pack` on a clean machine passes; after one byte of the pack's
  journal is changed, it fails.
- **SC-005** Two consecutive `render` runs on an unchanged `audit/` produce byte-identical files.
- **SC-006** Hook p95 ≤ 2 s on the examples/orders project with 200 events (CI measures it).
- **SC-007** The viewer opens from `file://` in Chrome, Edge and Firefox with the network disabled and
  renders the overview and a trail (Playwright smoke test).
- **SC-008** An agent's attempt to run `auditguard decide …` from its shell, or to write under `audit/`,
  is blocked with a message (hook payload tests for both tools).
- **SC-009** A deferral added, changed and removed across three `/speckit.plan` runs yields exactly
  `waiver.added`, `waiver.changed`, `waiver.removed` in that order with the reasons.
- **SC-010** `sprint close` on a sprint with an open escalation prints `[WARN]` in record mode and refuses
  in enforce mode; the seal of the record-mode close carries the warning.

---

## 11. Appendix

### A. Example `trail.md` (excerpt)

```markdown
# 001-place-order — Sprint S-2026-21

**Stages:** design ✔ signed 2026-10-08 by Tech lead · implement ● in progress (handover pending) · test ○
**Open items:** 1 escalation undecided (escalation-tasks-b.md) · 1 change outside recorded commands after sign-off · waiver WVR-0012 expires 2027-03-31
**Chain:** 41 events · head e91a… · sealed 2026-10-17 (seal f0a3…) · anchored audit/S-2026-21 ✔ · golden: 39 verified, 1 ephemeral, 1 unexplained commit

## Design — entered 2026-10-06 09:12 · completed 2026-10-08 14:00 (design.signed)
| # | When | Actor | Event | Detail | Evidence |
|---|------|-------|-------|--------|----------|
| 3 | 10-06 09:12 | agent claude | specify started → finished (pass) | spec.md created · 4 stories, 7 requirements | — |
| 7 | 10-06 11:40 | agent claude | plan started → finished (pass) | plan.md, research.md, data-model.md, contracts/orders.yaml · scopeGuard plan 11/11 (2 it.) · archiGuard plan-b PASS, 1 advisory | scopeguard-report.json, plan-b.json |
| 8 | 10-06 11:40 | script | waiver.added | FR-007 deferred — "PO decision 2026-10-05: phase 2" | plan.md rows |
| 12 | 10-07 09:05 | agent claude | tasks started → abandoned (escalated) | scope gate: US3 unresolved after 3 iterations | escalation-tasks-b.md |
| 13 | 10-07 10:30 | human Roman (lead architect) | escalation decided: accept | "US3 moves to sprint 22, RFI-14 to the BA" | — |
| 15 | 10-08 14:00 | human Tech lead | ★ design.signed | 6 artefacts hashed · evidence green | signoff.json |

## Implement — entered 2026-10-09 08:50
| # | When | Actor | Event | Detail | Evidence |
|---|------|-------|-------|--------|----------|
| 16 | 10-09 08:50 | agent claude | implement started | A4.1 entry pin check pass | implement-a.json |
| 19 | 10-09 13:22 | script | ⚠ artefact.changed | plan.md changed outside a command, after sign-off · author J. Doe · inferred: plan | — |
```

### B. Example `verify --golden` output

```text
auditGuard 0.1.0 | verify --golden | sprints: all | journals 7 · events 212 · evidence 143
internal : PASS   chains intact · seals match · evidence hashes match · register consistent
golden   : FAIL   git ok · handover ok · lock ok · ledger ok · workflow ok · tracker not configured
  G1 commits reachable        212 verified
  G2 artefact hashes          180 verified · 9 ephemeral · 1 mismatch
  G3 commits explained         54 explained · 1 unexplained
  G4 evidence authentic        61 verified
  G5 decisions vs sources       4 verified
  G6 seals anchored             2 verified
  G7 chain heads anchored       7 verified
  G8 recompute                 skipped (use --recompute)

FINDINGS
  1. [G3] unexplained_commit d4e5f6a  2026-10-09 13:20  J. Doe   specs/001-place-order/plan.md
        reproduce: git show --stat d4e5f6a
  2. [G2] mismatch  event a0b1c2 (S-2026-21/001-place-order #22)  specs/001-place-order/plan.md
        recorded c4d1…  golden aa00…   reproduce: git cat-file blob a1b2c3d:specs/001-place-order/plan.md | sha256sum

RESULT: FAIL | 1 mismatch, 1 unexplained commit | report: audit/verify-report.json
```

### C. Ubiquitous language (for the README and the deck)

*auditGuard records; it never decides.* — *A trail is a set of claims; the golden source is where the
claims are checked.* — *Sealed sprint, anchored seal, exportable pack.* — *What the agent did, and what
the hooks did not see.*
