# Example: orders

[`orders`](orders) is a Spec Kit project taken through two sprints with the real
[scopeGuard](https://github.com/rlgdev/spec-kit-scopeguard) and [archiGuard](https://github.com/rlgdev/spec-kit-archiguard)
engines while auditGuard recorded everything. Open [`orders/audit/viewer/index.html`](orders/audit/viewer/index.html)
in a browser, or start at [`orders/audit/index.md`](orders/audit/index.md).

| Sprint | What happens |
|--------|--------------|
| S-2026-21 (closed, sealed, anchored) | the BA hands over `001-place-order`; `/speckit.plan` - archiGuard finds a missing rule and the scope gate a missing requirement, both repaired; `/speckit.tasks` escalates (no progress on the layering fitness task) and the lead architect decides; a ledger waiver for ARCH-401 is approved; `/speckit.analyze`; the tech lead signs the design |
| S-2026-22 (open) | `/speckit.implement` - two fitness violations repaired; a person edits the signed plan outside any command (flagged, with the author); `/speckit.converge`; the tech lead approves implement; `002-refunds` is specified and planned with a deferral; a workflow spec-review gate is approved; the pull request of 001 is approved |

The project files are the state at the end of S-2026-22, after every repair: the planted violations that
[archiGuard's `examples/orders`](https://github.com/rlgdev/spec-kit-archiguard/tree/main/examples) starts from
(the missing ARCH-201 and BR-002 rows in `plan.md`, the two illegal imports in `src/`) are already fixed here,
`tasks.md` has its fitness tasks ticked and `.specify/archiguard/ledger.jsonl` holds the WVR-0012 waiver.
`tools/make-example.py` produces that end state; to see the gates fail, start from archiGuard's example.

The example contains the project files and `audit/`, not the git repository, so the golden checks are skipped
here. `render`, `show`, `check` and `collect` leave the example as shipped; `verify` (with or without `--golden`)
rewrites `audit/verify-report.json` and, unless you pass `--no-render`, the views - `audit/index.md`, every
`trail.md` and `audit/viewer/data.js` - with the golden column empty. Run `git checkout -- audit` inside `orders/`
to restore the shipped, golden-verified views. Regenerate the example with the full repository (history, anchor
notes, the `audit/S-2026-21` tag and a bare remote) to run the golden checks:

```bash
python tools/make-example.py --archiguard-src ../spec-kit-archiguard --scopeguard-src ../spec-kit-scopeguard \
       --keep-repo /tmp/orders
cd /tmp/orders && python .specify/extensions/auditguard/scripts/python/auditguard.py verify --golden --recompute
```
