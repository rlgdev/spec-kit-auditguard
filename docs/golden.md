# Verification against the golden sources

`auditguard verify` checks the trail itself. `auditguard verify --golden` also checks every claim the trail makes
against the systems the organisation treats as authoritative, and labels each event:

| Label | Meaning |
|-------|---------|
| `verified` | the claim agrees with its golden source |
| `ephemeral` | the recorded state never reached git: a working state that changed again before a commit (informational) |
| `unanchored` | the claim cannot be anchored yet: the commit is not pushed, the file never committed, no anchor note covers the event |
| `mismatch` | the claim contradicts the golden source |

`mismatch` and `unexplained_commit` fail the command (exit 1); `ephemeral` and `unanchored` do not.

## The checks

**internal** - every journal parses; every `hash` recomputes; `seq` is contiguous per chain across the sprint
folders and every `prev` links (a deleted line or sprint folder breaks it); lines are stored in order; every evidence
snapshot hashes to its reference; every seal matches its journals (count, head, first seq) and evidence, and no
journal was created or extended in a sealed sprint; the register is consistent (one open sprint, chronological,
no overlaps, every closed sprint sealed).

**G1 commits** - every `commit` exists and is contained in a branch of `golden.git.remote` (`verified`), only in a
local branch or tag (`unanchored`), or nowhere (`mismatch`: history was rewritten or the commit never pushed).

**G2 artefact hashes** - for every event, every artefact hash (and the new hash of every changed file) is compared
with the blob in git: at the event's commit, else in the first commit on any branch that has that content
(`anchored_at`). A recorded hash that git never had is `ephemeral` when the work tree was dirty, `mismatch` when it
was clean. The event's `anchored_at` is the commit where every recorded file has the recorded content.

**G3 commits explained** - every non-merge commit reachable from HEAD or the base branch since the first recorded
commit that touches `golden.git.explain_paths` (minus `exclude_paths` and `audit/`) must be explained: each file it
changed is either recorded by an event with exactly that content, or changed inside the time window of a recorded
command that lists the path; merge commits recorded as `commit.merged` are explained. Otherwise:
`unexplained_commit` with the commit, author, date and files.

**G4 evidence** - every snapshot whose source file is tracked equals that file at the event's commit or in a later
commit. Never committed: `unanchored`; overwritten before a commit: `ephemeral`.

**G5 decisions** - `design.signed` from archiGuard is found in the history of `gates/signoff.json`; the signed
`spec.md` hash equals `source.sha256` of the BA handover record (`golden.handover`) - otherwise
`signed_spec_differs_from_handover`; ledger waivers and ADR decisions point at an unchanged line of an intact,
hash-chained ledger; an archiGuard verdict's `pins.lock` equals the hash of the committed standards lock;
a workflow gate verdict is still in the run state.

**G6 seals** - every sealed sprint has an annotated tag `audit/<sprint>` (signed and `git tag -v` verified when
`golden.git.sign: true`) whose message carries the seal hash, on a commit that contains the same `seal.json`, with an
anchor note on that commit that repeats the seal hash.

**G7 chain heads** - the latest anchor note (`git notes --ref=refs/notes/auditguard`) lists the head of every chain
(`{chain: {seq, hash}}`); the chain must still have that event at that seq. A truncated or rewritten chain is a
`mismatch` even when it still links. Events after the last anchor are `unanchored`. Online, the check also reports
whether the notes ref is pushed.

**G8 recompute** (`--recompute`) - every scopeGuard `gate.verdict` whose artefacts are anchored is recomputed in a
temporary `git worktree` at that commit with `scopeguard.py report --json` and compared with the snapshot.

## Anchoring

```bash
auditguard anchor                           # note on HEAD: every chain head and seal
auditguard anchor --sprint S-2026-21        # + annotated tag audit/S-2026-21 (HEAD must contain the seal)
auditguard anchor --sprint S-2026-21 --push # + push refs/notes/auditguard and the tag to golden.git.remote
```

Anchor after a sprint close and, in CI, after every merge to the main branch. Fetch notes and tags before verifying
in CI: `git fetch origin "refs/notes/*:refs/notes/*" "refs/tags/*:refs/tags/*"`.

## The report

`audit/verify-report.json` holds the internal result, the golden counts per check, the label of every event with the
per-file detail and every finding with its reproduce command. The views and the viewer show the labels; re-run
`verify --golden` when the viewer says the report is older than the newest event.
