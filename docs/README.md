# RhetoriQ Documentation

This directory contains the durable documentation for the current RhetoriQ
runtime and its next delivery stages. Implementation status and runtime
qualification are deliberately separate: code may exist before a real service
stack has produced acceptance evidence.

## Start here

| Document | Use it for |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Runtime topology, service boundaries, research workflow, data flow, and current-versus-planned components. |
| [Data sources](DATA_SOURCES.md) | Source policy, connector status, acquisition transports, and provider requirements. |
| [Kafka](KAFKA.md) | Event schemas, topics, delivery guarantees, retention, DLQs, and replay. |
| [Operations](OPERATIONS.md) | Local service startup, health, recovery, replay, B4/B5 administration, and troubleshooting. |
| [Deployment](DEPLOYMENT.md) | Public deployment, release evidence, rollback, and the implemented-but-unexecuted AWS showcase boundary. |
| [AWS release readiness](RELEASE_READINESS.md) | Candidate identity, completed local evidence, and the ordered gates remaining before AWS. |
| [Pre-B6 guide](PRE_B6_GUIDE.md) | Workstation prerequisites, environment setup, current local progress, and the B6 readiness checklist. |
| [B6 operations](B6_OPERATIONS.md) | Full Helm/EKS topology, verified implementation progress, guarded kind/AWS execution, evidence, and teardown. |
| [Testing](TESTING.md) | Routine checks, disposable-stack acceptance, and quality gates. |
| [100K stress-test plan](100k%20stress%20test%20plan.md) | Standalone throughput experiment for a 100,000-documents-per-day claim. |
| [Roadmap](ROADMAP.md) | Milestone status, remaining acceptance work, and future platform work. |
| [A2 scorecard](evaluation/A2_SCORECARD.md) | Generated deterministic research benchmark checked by the evaluation command. |

The repository [README](../README.md) covers installation and everyday local
development. The running FastAPI application at `/docs` is the authoritative
HTTP endpoint reference. Configuration names and defaults are authoritative in
`backend/config.py` and `.env.production.example`; never copy real secret values
into documentation.

## Documentation rules

- State whether a capability is implemented, runtime-qualified, deployed, or
  only planned.
- Keep operational commands in `OPERATIONS.md`, deployment procedures in
  `DEPLOYMENT.md`, and milestone state in `ROADMAP.md`.
- Preserve evidence limitations and use “first observed in the available
  dataset” instead of claiming a true origin.
- Update relative links whenever a document is renamed or removed.
- Do not commit credentials, production URLs containing tokens, private source
  material, or unsanitized runtime output.
