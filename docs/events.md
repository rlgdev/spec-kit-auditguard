# Events

Every line of a `journal.jsonl` is one event. This page is the schema (version `v: 1`).

## Common fields

| Field | Meaning |
|-------|---------|
| `v` | schema version (1) |
| `seq` | position in the chain of the feature (or `_project`), counted across all sprint folders: 1, 2, 3, ... |
| `at` | when it happened, ISO 8601 with offset; taken from the source when it has a time (a sign-off, a workflow step) |
| `recorded` | when auditGuard wrote it, present only when it differs from `at` by a second or more |
| `sprint` | the sprint folder the event is stored in (`_unassigned` when no sprint was open) |
| `feature` | the feature directory (`specs/001-place-order`) or `null` for project events |
| `stage` | the stage of the command, or the stage the feature is in |
| `kind` | the event kind (below) |
| `command`, `outcome` | for command events: `speckit.plan`, `pass` / `fail` / `escalated` / `error` / `unknown` |
| `actor` | `{type: human \| agent \| ci \| script, id, role?, session?, via?}` |
| `source` | `hook:<event>`, `event:<name>`, `collector:<name>`, `reconcile`, `derived`, `cli`, `ci` |
| `commit`, `branch`, `dirty` | git HEAD, branch and whether tracked files had uncommitted changes |
| `artefacts` | `{repo path: sha256}` of the feature's design artefacts (or the project artefacts) at that moment |
| `changed` | `[{path, from, to}]` files the command (or the out-of-band change) changed - design and code; `null` = absent |
| `worktree` | `{path: sha256}` of code files with uncommitted changes |
| `data` | kind-specific fields (below) |
| `evidence` | `[{name, sha256, size, path, source_path, snapshot}]` - `path` is relative to the journal's folder |
| `prev`, `hash` | the chain: `hash` = SHA-256 of the canonical JSON (sorted keys, `,` `:` separators, UTF-8) of the event without `hash`; `prev` = the `hash` of the event with the previous `seq` (64 zeros for `seq` 1) |

Every hash of a file (artefacts, evidence, blobs in git) is the SHA-256 of its content with LF line endings and no
BOM (binary files unchanged), so a Windows checkout and git agree - the same hash archiGuard's sign-off records.

## Kinds

| Kind | `data` |
|------|--------|
| `sprint.opened`, `sprint.closed` | `id, name, start, end, goal, warnings[]` |
| `session.started`, `session.ended` | `session, agent, trigger / reason` |
| `command.started` | (the artefacts are top-level) |
| `command.finished`, `command.abandoned` | `duration_s, started_at, started_event, gates[], waiver_changes, started_missing?, outcome_file?, reason?` |
| `stage.entered`, `stage.reentered`, `stage.completed` | `stage, by_command / by_event / by_kind / by / after_stage, completed_at?` |
| `gate.verdict` | scopeGuard: `tool, gate, status, coverage, coverage_pct, pass, violations, waived, iterations, open_items, fingerprint`; archiGuard: `tool, step, command, status, blocking, advisory, waived, iterations, max_iterations, history[], checks[], escalation, pins, fingerprint` |
| `report.saved` | `tool, name, fingerprint` (architecture-compliance.md, analyze-report.md, scopeguard-report.md) |
| `escalation.raised`, `escalation.withdrawn` | `tool, note, note_sha256, gate, items[], todo` |
| `escalation.decided` | `subject, verdict (accept \| reject \| defer), by, role, reason, note, raised_event` |
| `waiver.added`, `.changed`, `.approved`, `.revoked`, `.superseded`, `.expired`, `.removed` | `id, source (scope \| conformance \| ledger), item / rule(s), reason, owner, approver, expires, status, record, before / after, changed_fields, ledger_line?` |
| `decision` | `subject (design, implement, pr, spec-change, escalation:…, gate:<step>, ledger:<id>), verdict, by, role, reason, …` |
| `design.signed`, `design.reopened`, `handover.4-5`, `test.exit`, `implement.approved`, `pr.approved` | milestones: `by, role, reason, hashes?, pins?, fingerprint?` |
| `artefact.changed` | `files, after_signoff, after_handover, inferred_command, authors{path: "name (sha)"}` (the changes are top-level) |
| `commit.merged` | `commit, author, date, subject, pr, base, files[]` |
| `note` | `text` |
| `ci.verified` | `internal, golden, report_sha256` |
| `collector.error`, `collector.ran` | `name, message / events` |
| `<plugin>.<kind>` | whatever a plug-in collector reports |
