# The viewer

`auditguard render --html` writes two files:

- `audit/viewer/index.html` - the application: one static file (HTML, CSS and JavaScript, ~60 KB), no network
  requests, no web fonts, copied unchanged from the extension.
- `audit/viewer/data.js` - `window.AUDITGUARD = {...}`: every event with its summary, the register, the stage status
  of every feature, the waivers in force, the open items, the verification report and the evidence up to
  `viewer.inline_kb` (default 64 KB) inline. Loaded with `<script src>`, so the viewer works from `file://`.

Open it from the file system, attach the folder to a review, or serve it: `auditguard serve` (localhost,
re-renders `data.js` when a journal changes; the page reloads its data). `render.html_on_hook: true` keeps it current
after every hook. An audit pack (`auditguard export`) carries the viewer scoped to its sprint.

| Route | Shows |
|-------|-------|
| `#/` | features × sprints with the stages each feature moved through, flags, sealed sprints, verification badges |
| `#/sprint/<id>` | the seal and its anchor, the features, the decisions, waiver changes, out-of-band changes and gate verdicts of the sprint |
| `#/feature/<key>[/<sprint>]` | the trail as a chain by stage band; filters by stage, kind, actor and golden label; search (`/`); each event opens to its record (files with golden status, evidence, chain fields, permalink, the raw journal line) |
| `#/event/<hash12>` | a permalink to one event |
| `#/decisions` | what waits for a person (with the command to decide it) and every recorded decision |
| `#/waivers` | waivers in force by expiry (expiring soon highlighted) and the full history |
| `#/evidence` | every version of every report, viewable, with a compare of the last two |
| `#/verification` | the internal result, G1-G8 and the findings with copyable reproduce commands |

Light and dark follow the system (`viewer.theme` or the Theme button override it); the page prints cleanly; keyboard:
`/` search, `Esc` closes.
