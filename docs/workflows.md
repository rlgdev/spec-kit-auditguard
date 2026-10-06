# Workflows

The siblings' workflows (`scopeguard-sdd`, `archiguard-sdd`) need no change: every `type: gate` step that got a
verdict is collected from `.specify/workflows/runs/<run>/` as a `decision` with subject `gate:<step-id>`. The person
is taken from a workflow input such as `design_authority`; otherwise it is the git user, marked
`confidence: inferred`. To name the person explicitly, add a shell step after the gate:

```yaml
  - id: record-design-signoff
    type: shell
    run: "bash .specify/extensions/auditguard/scripts/bash/auditguard.sh decide gate:design-signoff {{ inputs.design_verdict }} --by \"{{ inputs.design_authority }}\" --feature-dir {{ inputs.feature }}"
```

With `integration: workflow` the Spec Kit hooks stay off and the workflow records the commands itself:

```yaml
  - id: audit-before-plan
    type: shell
    run: "bash .specify/extensions/auditguard/scripts/bash/auditguard.sh hook before_plan --via workflow --feature-dir {{ inputs.feature }}"
  - id: plan
    command: speckit.plan
  - id: audit-after-plan
    type: shell
    run: "bash .specify/extensions/auditguard/scripts/bash/auditguard.sh hook after_plan --via workflow --feature-dir {{ inputs.feature }}"
```

A given event is recorded exactly once: the path that is not configured prints `skipped`.
