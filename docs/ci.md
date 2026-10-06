# CI

The required check verifies; it never records (except `--record` on the main branch) and never anchors on pull
requests.

## GitHub Actions

```yaml
name: audit
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: write          # anchor pushes the notes ref on main; use read for pull requests only
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with: { fetch-depth: 0 }
      - run: git fetch -q origin "refs/notes/*:refs/notes/*" "refs/tags/*:refs/tags/*" || true
      - uses: rlgdev/spec-kit-auditguard@v0.1.0
        with:
          command: verify
          golden: "true"
  anchor:
    if: github.ref == 'refs/heads/main'
    needs: verify
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with: { fetch-depth: 0 }
      - run: |
          git fetch -q origin "refs/notes/*:refs/notes/*" || true
          git config user.name "audit-bot" && git config user.email "audit-bot@users.noreply.github.com"
      - uses: rlgdev/spec-kit-auditguard@v0.1.0
        with:
          command: anchor
          push: "true"
```

Action inputs: `command` (`verify` | `check` | `render` | `anchor`), `golden` (`"true"`), `recompute` (`"false"`),
`sprint` (for `anchor --sprint` and `export`), `push` (for `anchor`), `summary` (write the result to the job summary),
`engine` (`installed` = the project's `.specify/extensions/auditguard`, else this action's), `working-directory`,
`args` (extra arguments).

## Bitbucket Pipelines

```yaml
pipelines:
  pull-requests:
    '**':
      - step:
          name: audit trail
          image: python:3.12
          clone: { depth: full }
          script:
            - git fetch origin "+refs/notes/*:refs/notes/*" "+refs/tags/*:refs/tags/*" || true
            - python .specify/extensions/auditguard/scripts/python/auditguard.py verify --golden
```

## Publishing the viewer

```yaml
      - run: python .specify/extensions/auditguard/scripts/python/auditguard.py render --html
      - uses: actions/upload-pages-artifact@v3
        with: { path: audit }
      - uses: actions/deploy-pages@v4
```

The viewer is at `<pages url>/viewer/`. The trail can name people and decisions - publish it where its readers
are allowed to see it.
