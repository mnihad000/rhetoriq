# RhetoriQ Documentation

This directory records the smaller runtime and the deployment work on the
`deployment` branch. Implementation, tests, runtime qualification, and public
deployment remain separate claims.

## Start here

| Document | Use it for |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Current database-queue runtime, target deployment, data flow, and service boundaries. |
| [Deployment](DEPLOYMENT.md) | Current deployment status, transition boundary, and launch checks. |
| [Simplification plan](DEPLOYMENT_SIMPLIFICATION.md) | Agreed removals, retained product capabilities, open decisions, and acceptance criteria. |
| [Data sources](DATA_SOURCES.md) | Source policy, connector status, acquisition transports, and provider requirements. |
| [Operations](OPERATIONS.md) | Compose startup, database-job recovery, B5 administration, and troubleshooting. |
| [Testing](TESTING.md) | Functional checks and database-job acceptance. |
| [Roadmap](ROADMAP.md) | Delivery status and remaining migration work. |
| [Release readiness](RELEASE_READINESS.md) | Branch regression evidence and remaining deployment gates. |
| [A2 scorecard](evaluation/A2_SCORECARD.md) | Deterministic research benchmark checked by the evaluation command. |

Historical AWS/Kubernetes, Kafka, and Flink runbooks and execution tooling
remain on the `further_dev` reference branch. The current readiness record
does not claim a qualified permanent deployment.

## Documentation rules

- State whether a capability is implemented, tested, runtime-qualified, or
  deployed.
- Keep operational commands in `OPERATIONS.md`, deployment decisions in
  `DEPLOYMENT.md`, and the smaller target in `DEPLOYMENT_SIMPLIFICATION.md`.
- Preserve evidence limitations and use “first observed in the available
  dataset” instead of claiming a true origin.
- Keep provider credentials, private source material, and unsanitized runtime
  output out of committed documentation.
