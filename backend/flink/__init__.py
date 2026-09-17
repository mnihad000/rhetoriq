"""PyFlink stream processing primitives for the B4 document pipeline.

The modules in this package deliberately keep the deterministic processing
engine independent of a running Kafka/Flink cluster.  The application entry
point in :mod:`flink.job` adapts the same engine to PyFlink operators.
"""

from .contracts import (
    ENRICHED_DOCUMENTS_TOPIC,
    ENRICHMENT_REQUESTED_TOPIC,
    EVALUATION_HEARTBEATS_TOPIC,
    LATE_DOCUMENTS_TOPIC,
    PROCESSED_DOCUMENTS_TOPIC,
    RAW_DOCUMENTS_TOPIC,
    SIGNALS_DETECTED_TOPIC,
)
from .stream_engine import EngineConfig, EngineOutput, StreamIntelligenceEngine

__all__ = [
    "EngineConfig",
    "EngineOutput",
    "StreamIntelligenceEngine",
    "RAW_DOCUMENTS_TOPIC",
    "ENRICHMENT_REQUESTED_TOPIC",
    "ENRICHED_DOCUMENTS_TOPIC",
    "PROCESSED_DOCUMENTS_TOPIC",
    "SIGNALS_DETECTED_TOPIC",
    "LATE_DOCUMENTS_TOPIC",
    "EVALUATION_HEARTBEATS_TOPIC",
]
