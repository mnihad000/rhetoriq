# Contributing to RhetoriQ

## Principles

Keep services independently deployable, preserve event and API contracts, and never weaken the evidence-first safety rules for convenience. Documentation changes must update the relevant architecture or service contract when they change a target design decision.

## Development Workflow

1. Start with the relevant target document in `docs/` and identify affected contracts.
2. Make one focused change set per service or interface.
3. Add or update unit, integration, and contract tests.
4. Run the verification steps in [Testing](docs/TESTING.md).
5. Document new environment variables, Kafka topics, schemas, storage fields, and operational metrics.

## Contribution Boundaries

- Do not commit credentials, production data, or source material that violates provider terms.
- Do not add model-generated claims without source receipts and limitations.
- Do not introduce a direct service-to-service dependency when an event or stable API contract is the intended boundary.
- Do not change a canonical document, event, or investigation schema without versioning and migration notes.
