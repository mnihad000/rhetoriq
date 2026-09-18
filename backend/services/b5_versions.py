"""Material projection-method identities captured in immutable snapshots."""

GRAPH_METHOD_VERSION = "b5-graph-v1"
MUTATION_METHOD_VERSION = "b5-mutation-v1"
AMPLIFICATION_METHOD_VERSION = "b5-timeline-amplification-v1"


def projection_methods() -> dict[str, str]:
    return {"graph": GRAPH_METHOD_VERSION, "mutation": MUTATION_METHOD_VERSION,
            "amplification": AMPLIFICATION_METHOD_VERSION}
