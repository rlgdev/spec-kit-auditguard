# Collectors

Collectors run at every hook and on `auditguard collect`. Each reads what another tool left in the repository,
compares it with what the chain already records - the chain is the collector's memory, so a second run with no
change records nothing - and snapshots what is new as evidence. A collector never runs another tool's gates; a
failing collector is recorded as `collector.error` and never stops a hook.

| Collector | Enabled | Reads | Records |
|-----------|---------|-------|---------|
| `git` | `collectors.git.enabled` | HEAD, branch, dirty files, the design artefacts (`collectors.git.artefacts`, relative to the feature), the project artefacts, the code (`collectors.git.code`); merge commits into the base | the git fields of every event, `changed`, `commit.merged` |
| `scopeguard` | `auto` (installed) | `scopeguard.py report --json` (read-only), `.scopeguard/history-<gate>.json`, `scopeguard-escalation-*.md`, `scopeguard-report.md` | `gate.verdict` per phase with coverage and iterations, `escalation.*`, `report.saved` |
| `archiguard` | `auto` (installed) | `gates/<command>-<a\|b\|verify>.json` and the check verdicts they name, `gates/escalation-*.md`, `gates/signoff.json`, `gates/signoff-history/*.json`, `gates/handover-4-5.json`, `gates/test-loop.json`, `gates/architecture-compliance.md`, `gates/analyze-report.md` | `gate.verdict` with the iteration history, `escalation.*`, `decision` + `design.signed` / `design.reopened`, `handover.4-5`, `test.exit`, `report.saved` |
| waivers | always | `## Scope Coverage` of plan.md and tasks.md (deferred rows), `## Architecture Conformance` of plan.md (`deviation`, `not applicable`), `.specify/archiguard/ledger.jsonl` | `waiver.*` per change; ADR status changes as `decision` (`ledger:<id>`) |
| `workflow` | `collectors.workflow.enabled` | `.specify/workflows/runs/<run>/state.json`, `inputs.json`, `log.jsonl` | `decision` (`gate:<step-id>`) for every gate step with a verdict; the person from a workflow input such as `design_authority` |

Ledger entries scoped to features (`--feature`) are recorded in those features' chains; unscoped ones in `_project`.

## Plug-in contract

```yaml
collectors:
  jira:
    command: tools/jira-collector.py     # relative to the project root or to .specify/extensions/
    timeout: 60
```

Invocation, from the project root: `<command> [--feature-dir specs/<feature>] --since <iso> --json` (`.py` runs with
auditGuard's Python). Print a JSON list:

```json
[{"kind": "issue.created", "at": "2026-10-07T10:00:00+02:00", "actor": {"type": "script", "id": "jira"},
  "data": {"key": "ORD-12", "summary": "Place an order"}, "evidence": [{"name": "ORD-12.json", "path": "tmp/ORD-12.json"}]}]
```

Exit `0` with events, `0` with `[]` for nothing new, anything else on failure (recorded as `collector.error`).
auditGuard adds the sprint, the feature, the stage, the git fields and the chain fields, snapshots the evidence and
namespaces the kind (`jira.issue.created`) unless it is a core kind (`decision`, `note`, `gate.verdict`, ...).
`--since` is the time of the last run that reported events.
