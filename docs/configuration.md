# Configuration

`.specify/extensions/auditguard/auditguard-config.yml` is created at install from
[`config-template.yml`](../config-template.yml), committed, and changed through a pull request. Every key is optional;
unknown keys are errors, so a typo never switches something off silently.

| Key | Default | Meaning |
|-----|---------|---------|
| `version` | `1` | the format version of this file |
| `integration` | `hooks` | `hooks`: the 20 Spec Kit hooks record. `workflow`: the hooks print `skipped`; workflow shell steps (`auditguard hook <event> --via workflow`) and CI record. Run `configure` after changing it. |
| `mode` | `record` | `record`: never blocks. `enforce`: `check` and `sprint close` fail on a broken rule; a hook that cannot record exits 2. |
| `audit.root` | `audit` | the audit folder |
| `audit.register` | `audit/sprints.yml` | the sprint register |
| `audit.unassigned`, `audit.project` | `_unassigned`, `_project` | folder names (reserved) |
| `audit.evidence.snapshot` | `true` | keep content-addressed snapshots of the reports events reference |
| `audit.evidence.max_file_kb` | `512` | larger files: hash and source path only |
| `render.on_hook` | `true` | rebuild the Markdown views after every hook |
| `render.html_on_hook` | `false` | also rebuild `audit/viewer/data.js` after every hook |
| `render.expiring_days` | `30` | waivers expiring within this window are open items |
| `viewer.inline_kb` | `64` | evidence embedded in the viewer up to this size |
| `viewer.theme` | `auto` | `auto`, `light`, `dark` |
| `sessions.record` | `true` | record agent sessions and put the session id on agent events |
| `stages.<name>.commands` | design / implement / test | the Spec Kit commands of the stage (without `speckit.`) |
| `stages.<name>.completed_by` | milestones | the event kinds that complete the stage; a following stage without commands is entered when its predecessor completes |
| `collectors.git.enabled` | `true` | the git fields of every event, `changed` and `commit.merged` |
| `collectors.git.artefacts` | spec, plan, research, data-model, quickstart, tasks, handover, contracts/**, checklists/** | design artefacts, relative to the feature |
| `collectors.git.project_artefacts` | `.specify/memory/constitution.md` | artefacts of the project chain |
| `collectors.git.code` | `src/** app/** lib/** tests/** test/**` | documentation of the code paths (the code tracked for changes is `golden.git.explain_paths` outside `specs/`) |
| `collectors.scopeguard` | `enabled: auto`, `version: ">=0.4,<0.6"`, `report: true`, `timeout: 120` | |
| `collectors.archiguard` | `enabled: auto`, `version: ">=0.1,<0.3"`, `ledger: .specify/archiguard/ledger.jsonl` | |
| `collectors.workflow` | `enabled: true`, `runs: .specify/workflows/runs` | |
| `collectors.<name>` | - | a plug-in: `command`, `timeout` (`60`), `enabled` (`true`); `version` is accepted and not checked ([collectors.md](collectors.md)) |
| `golden.git.enabled` | `true` | accepted; not read in 0.1.0 - the git checks run whenever the project is a git repository |
| `golden.git.remote`, `golden.git.base` | `origin`, `main` | G1 reachability, G3 range, merges |
| `golden.git.explain_paths` | `specs/** src/** app/** lib/** tests/** test/** .specify/memory/**` | paths every commit on must be explained (G3) and that are tracked for out-of-band changes |
| `golden.git.exclude_paths` | `specs/*/gates/** specs/*/.scopeguard/** specs/*/scopeguard-*.md audit/**` | written by the gates and auditGuard themselves |
| `golden.git.notes_ref` | `refs/notes/auditguard` | anchor notes |
| `golden.git.sign` | `false` | sign the `audit/<sprint>` tags and verify their signatures |
| `golden.handover`, `golden.lock`, `golden.ledger` | archiGuard's paths | G5 sources |
| `golden.tracker` | `null` | `{command: ..., timeout: 120}` - called with `--check <json list of issue keys>`, prints `{key: true\|false}` |
| `actors.human` | `auto` | `git user.name`; `--by` and `AUDITGUARD_ACTOR` win |
| `actors.agent` | `auto` | the integration in `.specify/init-options.json` |
| `actors.ci_env` | `GITHUB_ACTOR`, ... | variables naming the CI actor |
| `rules.*` | all on except `anchored_before_export` | the completeness rules of `check` / `sprint close` |
| `guard.enabled`, `guard.readonly`, `guard.human_only` | on, `audit/**` + the extension, decide / note / sprint open / sprint close / anchor | the `pre_tool_use` guard |

## Overrides

- `.specify/extensions/auditguard/local-config.yml` (workstation, not committed): `integration`, `render.on_hook`,
  `render.html_on_hook`, `viewer.*`. Other keys are ignored with a note.
- Environment: `AUDITGUARD_MODE`, `AUDITGUARD_INTEGRATION`, `AUDITGUARD_ACTOR`, `AUDITGUARD_CONTEXT=agent` (set by the
  hook commands; makes `decide`, `note`, `sprint open|close` and `anchor` exit 3), `AUDITGUARD_PYTHON` (launchers).
- With `CI=true`, `GITHUB_ACTIONS=true` or `AUDITGUARD_CI=1` (the GitHub Action sets it) the local file and
  `AUDITGUARD_INTEGRATION` are ignored.

## Rules

| Rule | Broken when |
|------|-------------|
| `implement_requires_design_signed` | `/speckit.implement` started while the design was not signed (no `design.signed`, or re-opened since) |
| `waiver_requires_approver` | a ledger waiver in force has no approver, or a deferral / deviation has no reason |
| `escalations_decided_before_close` | an escalation was raised and neither decided (`auditguard decide escalation:<note> ...`) nor withdrawn |
| `no_changes_after_signoff` | an `artefact.changed` with `after_signoff` |
| `commands_closed_before_close` | a command started and never finished or was closed |
| `no_unassigned_events` | events were recorded while no sprint was open |
| `anchored_before_export` | (off by default) `export` refuses a sprint whose seal is not anchored |
