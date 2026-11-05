# Loop feedback - specs/001-place-order

Not blocking: what this feature did that the domain master design does not describe, for the next iteration.
archiGuard 0.2.0 · after implement step B · commit 6418374 · domain map 1.4.0 · repository origin

## Topology: new service or existing one?

**Answer: existing.** The feature extends the existing unit `.`. The domain master design declares no deployable for orders.

| Unit | New on this branch | Contexts of the changed code | Code files | Declared deployable |
|---|---|---|---|---|
| `.` (pom.xml) | no | orders (home), payments | 6 | none |

## Items (4: 0 high, 2 medium, 2 low)

| Id | Impact | Kind | Subject | Suggestion for the next iteration | For |
|---|---|---|---|---|---|
| LF-885c1908 | medium | cross-context | orders->payments | 2 files of payments changed by this orders feature under the customer-supplier relation: plan the change as a payments backlog item owned by Payments domain owner, or record a partnership / shared kernel if the two contexts evolve together. | Payments domain owner, lead architect |
| LF-2ba4e67d | medium | topology | orders:undeclared | Declare the deployment unit(s) of orders in the domain map (contexts.orders.deployable, deployables.<name>), so the next feature can tell a new service from an existing one. | lead architect |
| LF-831ba84c | low | vocabulary | orders:shipment | Add Shipment to the glossary of orders (or as a synonym of an existing term) in the next domain map version, or rename it in the data model and contracts. | Orders domain owner, BA |
| LF-c90bb238 | low | map-gap | customers:no-code | Declare the code paths of customers (contexts.customers.code) so the context-boundary fitness function covers it. | lead architect |

## Each item in detail

- **LF-885c1908** (medium, cross-context) **The orders feature changed code of payments** - 2 changed code files belong to context 'payments' (owner: Payments domain owner); the feature's home context is 'orders'. Suggestion: 2 files of payments changed by this orders feature under the customer-supplier relation: plan the change as a payments backlog item owned by Payments domain owner, or record a partnership / shared kernel if the two contexts evolve together. Target: domain-map: relations. For: Payments domain owner, lead architect. Occurrences (2): `src/main/java/com/acme/payments/api/PaymentsClient.java`, `src/main/java/com/acme/payments/internal/PaymentGateway.java`
- **LF-2ba4e67d** (medium, topology) **The deployment unit of orders is not declared** - Context 'orders' declares no deployable in domain map 1.4.0; this feature changed code in the unit(s) '.'. Suggestion: Declare the deployment unit(s) of orders in the domain map (contexts.orders.deployable, deployables.<name>), so the next feature can tell a new service from an existing one. Target: domain-map: contexts.orders.deployable, deployables. For: lead architect. Occurrences (1): `.`
- **LF-831ba84c** (low, vocabulary) **'Shipment' is not a term of orders** - The data model or a contract uses 'Shipment', which is not in the glossary, the owned entities or the synonyms of 'orders' (or of a related context) in domain map 1.4.0. Suggestion: Add Shipment to the glossary of orders (or as a synonym of an existing term) in the next domain map version, or rename it in the data model and contracts. Target: domain-map: contexts.orders.glossary. For: Orders domain owner, BA. Occurrences (1): `data-model.md:15`
- **LF-c90bb238** (low, map-gap) **Context customers declares no code paths** - Context 'customers' has no code paths in domain map 1.4.0, so A4.4 cannot attribute its files (it declares the modules com.acme.customers). Suggestion: Declare the code paths of customers (contexts.customers.code) so the context-boundary fitness function covers it. Target: domain-map: contexts.customers.code. For: lead architect.

## Resolved during this feature

None.
