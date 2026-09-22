"""Fail static B6 validation when a rendered profile loses a required contract."""

from __future__ import annotations

import argparse
import sys

import yaml


STATEFUL = {"postgres", "kafka", "elasticsearch", "neo4j", "flink-jobmanager"}
DEPLOYMENTS = {
    "apicurio",
    "redis",
    "searxng",
    "flink-taskmanager",
    "api",
    "frontend",
    "outbox-publisher",
    "document-worker",
    "signal-worker",
    "investigation-worker",
    "projection-worker",
    "enrichment-worker",
    "elasticsearch-worker",
    "neo4j-worker",
    "minilm-worker",
}
SERVICES = {
    "postgres",
    "kafka",
    "kafka-headless",
    "apicurio",
    "elasticsearch",
    "neo4j",
    "redis",
    "searxng",
    "flink-jobmanager",
    "api",
    "frontend",
}
PVCS = {
    "postgres",
    "kafka",
    "elasticsearch",
    "neo4j",
    "flink-checkpoints",
    "flink-savepoints",
    "evidence",
}


def fail(message: str) -> None:
    raise SystemExit(f"B6 render validation failed: {message}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["kind", "eks-demo"], required=True)
    args = parser.parse_args()
    documents = [document for document in yaml.safe_load_all(sys.stdin) if document]
    keyed = {(document.get("kind"), document.get("metadata", {}).get("name")): document for document in documents}

    actual_stateful = {name for kind, name in keyed if kind == "StatefulSet"}
    actual_deployments = {name for kind, name in keyed if kind == "Deployment"}
    actual_services = {name for kind, name in keyed if kind == "Service"}
    actual_pvcs = {name for kind, name in keyed if kind == "PersistentVolumeClaim"}
    if actual_stateful != STATEFUL:
        fail(f"StatefulSet topology drift: {sorted(actual_stateful ^ STATEFUL)}")
    if actual_deployments != DEPLOYMENTS:
        fail(f"Deployment topology drift: {sorted(actual_deployments ^ DEPLOYMENTS)}")
    if actual_services != SERVICES:
        fail(f"Service topology drift: {sorted(actual_services ^ SERVICES)}")
    if actual_pvcs != PVCS:
        fail(f"PVC topology drift: {sorted(actual_pvcs ^ PVCS)}")

    for kind in ("Deployment", "StatefulSet"):
        for (actual_kind, name), workload in keyed.items():
            if actual_kind != kind:
                continue
            if workload["spec"].get("replicas") != 1:
                fail(f"{kind}/{name} must have exactly one replica")
            if kind == "Deployment" and workload["spec"].get("strategy", {}).get("type") != "Recreate":
                fail(f"Deployment/{name} must use Recreate")
            containers = workload["spec"]["template"]["spec"].get("containers", [])
            for container in containers:
                image = container.get("image", "")
                if "@sha256:" not in image:
                    fail(f"{kind}/{name} uses a non-digest image")
                resources = container.get("resources", {})
                if not resources.get("requests") or not resources.get("limits"):
                    fail(f"{kind}/{name} lacks explicit requests/limits")
                if not container.get("readinessProbe"):
                    fail(f"{kind}/{name} lacks a readiness probe")
                if not container.get("securityContext"):
                    fail(f"{kind}/{name} lacks a container security context")

    for name in SERVICES:
        service = keyed[("Service", name)]
        if name != "kafka-headless" and service["spec"].get("type", "ClusterIP") != "ClusterIP":
            fail(f"Service/{name} is public")
    if any(kind == "Secret" for kind, _name in keyed):
        fail("chart must not render operator-managed credential Secrets")

    ingresses = [document for document in documents if document.get("kind") == "Ingress"]
    paths = {
        path["path"]
        for ingress in ingresses
        for rule in ingress["spec"].get("rules", [])
        for path in rule.get("http", {}).get("paths", [])
    }
    if paths and paths != {"/", "/api"}:
        fail(f"unexpected public ingress paths: {sorted(paths)}")
    if len([document for document in documents if document.get("kind") == "NetworkPolicy"]) < 5:
        fail("required NetworkPolicies are missing")

    jobs = {name for kind, name in keyed if kind == "Job"}
    for prefix in ("migrations-", "topics-", "b5-init-"):
        if not any(name.startswith(prefix) for name in jobs):
            fail(f"required Job prefix is missing: {prefix}")
    print(f"B6 {args.profile} render contract passed ({len(documents)} resources).")


if __name__ == "__main__":
    main()
