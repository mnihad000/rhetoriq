# Ephemeral AWS Kubernetes Portfolio Demonstration

## Purpose

RhetoriQ will use two deliberately different deployment modes:

1. **Portfolio cloud demonstration:** a short-lived, production-style AWS Kubernetes environment used to demonstrate cloud-native engineering work.
2. **Public demo:** a low-cost lightweight deployment, such as Railway or Render, intended for visitors to use after the AWS environment has been destroyed.

The AWS environment is evidence of technical capability, not a permanently operated production service.

## Portfolio cloud demonstration

Provision the environment with Terraform and deploy it to Amazon EKS. Keep the workload intentionally small and use seeded or synthetic documents; do not represent this demo as a continuously operating 100K-documents-per-day production system.

The demo should show:

- Container images, Kubernetes manifests or Helm charts, ConfigMaps, Secrets, resource requests, readiness probes, liveness probes, and autoscaling configuration.
- Frontend, API, research worker, and a minimal event-processing path.
- The applicable Track B components: PostgreSQL/pgvector, Kafka contracts, stream-processing demonstration, provenance/search services, and observability when they have been implemented.
- GitHub Actions validation and deployment flow, Terraform infrastructure, and Argo CD/GitOps only after those artifacts exist.
- Prometheus metrics and Grafana dashboards for latency, investigation status, worker health, event flow, and model/API failures.

The record of the demonstration must include an end-to-end seeded investigation, dashboard screenshots, architecture diagrams, a deployment log, and a documented teardown result.

## Public demo

The public deployment stays separate from EKS:

- Host the frontend and lightweight FastAPI backend on Railway or Render.
- Use a hosted verifier such as Gemini only within quota and with deterministic validation/fail-closed publication rules.
- Enforce investigation rate limits and protect API credentials with platform secrets.
- Use durable storage appropriate to the chosen host; do not rely on an ephemeral filesystem for persisted investigations.

This deployment prioritizes accessibility and low cost over demonstrating the full distributed architecture.

## AWS cost and safety controls

Amazon EKS has a per-cluster hourly charge, and worker nodes, load balancers, volumes, NAT gateways, public IP addresses, data transfer, logs, and managed data services can also incur charges. Use AWS credits where available, but do not treat the showcase as permanently free.

Before provisioning:

- Set an AWS Budget alert at a low threshold and configure billing notifications.
- Use a dedicated demo account or clearly isolated Terraform workspace.
- Restrict regions, instance sizes, replica counts, storage, and log retention in Terraform.
- Do not create NAT gateways or managed services unless the demo explicitly needs them.
- Add resource tags: `project=rhetoriq`, `environment=portfolio-demo`, and an `expires-at` timestamp.

After recording the demo:

1. Export screenshots, logs, dashboard snapshots, and benchmark results.
2. Run `terraform destroy` from the same workspace used for provisioning.
3. Confirm that the EKS cluster, node groups, load balancers, volumes, public IPs, NAT gateways, and managed databases are deleted.
4. Review AWS Cost Explorer and active resources before closing the demo window.

## Resume guidance

Use claims that match the evidence collected during the demo:

- "Designed and deployed a production-style, ephemeral Kubernetes demonstration environment on AWS."
- "Implemented and demonstrated [measured throughput] using seeded or synthetic workloads."
- "Designed the target architecture for Kafka, Flink, pgvector, Neo4j, Kubernetes, and observability; identify each component as implemented only after its roadmap phase is complete."

Do not claim permanent production scale, continuous 100K+ daily-document processing, LangChain usage, or operational microservice counts unless the repository, measurements, and recorded demonstration support the claim.

## Current boundary

This is the approved deployment strategy, not an implementation claim. The current repository does not yet contain the Track B Kafka, Flink, PostgreSQL/pgvector, Elasticsearch, Neo4j, Kubernetes, Terraform, Argo CD, Prometheus, or Grafana runtime implementations. Build and document those components phase by phase before using them in the AWS demonstration.
