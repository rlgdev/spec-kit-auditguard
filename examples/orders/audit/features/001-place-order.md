# 001-place-order — all sprints

**Stages:** design ✔ 2026-10-08 by Tech lead · implement ✔ 2026-10-21 by Tech lead · test ✔ 2026-10-23 by Tech lead  
**Open items:** WVR-0012 expires 2026-10-31 · 3 loop-feedback item(s) untriaged (1 deferred)

| Sprint | Stages at the end of the sprint | Events | Seq |
|---|---|---|---|
| [S-2026-21](../sprints/S-2026-21/001-place-order/trail.md) | design ✔ 2026-10-08 by Tech lead · implement ○ · test ○ | 26 | #1–#26 |
| [S-2026-22](../sprints/S-2026-22/001-place-order/trail.md) | design ✔ 2026-10-08 by Tech lead · implement ✔ 2026-10-21 by Tech lead · test ✔ 2026-10-23 by Tech lead | 21 | #27–#47 |

## Waivers in force

| Id | Source | Item / rule | Status | Reason | Approver | Expires |
|---|---|---|---|---|---|---|
| WVR-0012 | ledger | ARCH-401 | approved | Payments sandbox credentials stay in the test configuration until the vault client ships | Lead architect | 2026-10-31 |

## Loop feedback

| Id | Impact | Kind | Subject | Suggestion for the next iteration | For | Status |
|---|---|---|---|---|---|---|
| LF-885c1908 | medium | cross-context | orders->payments | 2 files of payments changed by this orders feature under the customer-supplier relation: plan the change as a payments backlog item owned by Payments domain owner, or record a partnership / shared kernel if the two contexts evolve together. | domain owner (Payments domain owner), lead architect | new |
| LF-2ba4e67d | medium | topology | orders:undeclared | Declare the deployment unit(s) of orders in the domain map (contexts.orders.deployable, deployables.<name>), so the next feature can tell a new service from an existing one. | lead architect | in-backlog ARCH-77 |
| LF-831ba84c | low | vocabulary | orders:shipment | Add Shipment to the glossary of orders (or as a synonym of an existing term) in the next domain map version, or rename it in the data model and contracts. | domain owner (Orders domain owner), BA | deferred |
| LF-c90bb238 | low | map-gap | customers:no-code | Declare the code paths of customers (contexts.customers.code) so the context-boundary fitness function covers it. | lead architect | new |

Triage: `auditguard decide feedback:<id> accept|reject|defer --by <name> [--ref <tracker key>]` · backlog: `auditguard backlog`
