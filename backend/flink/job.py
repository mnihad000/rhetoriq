"""Flink application: pure normalization, content dedup, then phrase state.

Hosted inference runs independently. Operators use checkpoint-managed state;
Java serialization schemas write byte keys and Confluent-framed values.
"""
from __future__ import annotations
import hashlib
import os
from datetime import datetime, timezone
from typing import Any
from .serialization import ConfluentJsonAdapter
from .stream_engine import StreamIntelligenceEngine

try:
    from pyflink.datastream.functions import FlatMapFunction, KeyedProcessFunction, MapFunction
    from pyflink.common.watermark_strategy import TimestampAssigner
except ImportError:  # Offline fixture imports do not require a PyFlink host.
    class FlatMapFunction: pass
    class KeyedProcessFunction: pass
    class MapFunction: pass
    class TimestampAssigner: pass


def event_timestamp(event: dict[str, Any]) -> int:
    document = (event.get("payload") or {}).get("document") or {}
    value = document.get("published_at") or document.get("collected_at") or event.get("occurred_at")
    if not value:
        raise ValueError("A stream document requires an event timestamp")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _watermark(ctx: Any) -> datetime | None:
    value = ctx.timer_service().current_watermark()
    return datetime.fromtimestamp(value / 1000, timezone.utc) if value > -9_000_000_000_000_000 else None


def _dead_letter(topic: str, value: bytes | dict, error: Exception) -> dict:
    import json
    from models.events import DeadLetterEvent, DeadLetterPayload
    material = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    digest = hashlib.sha256(material).hexdigest()
    event = value if isinstance(value, dict) else {}
    return DeadLetterEvent.create(
        DeadLetterPayload(original_topic=topic, original_event_id=event.get("event_id"),
                          payload_sha256=digest, consumer="rhetoriq-b4-flink",
                          failure_class=type(error).__name__, failure_code="invalid_stream_contract",
                          error_message="Stream contract validation failed", attempt_count=1,
                          failed_at=datetime.now(timezone.utc)),
        event_id=f"flink_dlq_{digest[:32]}", producer="rhetoriq-b4-flink",
        correlation_id=event.get("correlation_id") or digest,
        partition_key=event.get("partition_key") or digest,
    ).model_dump(mode="json")


class DecodeAndNormalize(FlatMapFunction):
    def __init__(self, topic: str):
        self.topic = topic

    def open(self, _context):
        from services.kafka_runtime import SchemaRegistry
        self.registry = SchemaRegistry()
        self.registry.ensure_schema(self.topic)

    def flat_map(self, frame):
        import httpx
        from models.events import dlq_topic, validate_event
        value = bytes(frame)
        try:
            decoded = self.registry.decode(self.topic, value)
            event = validate_event(self.topic, decoded)
            if self.topic == "raw.documents.v1":
                from services.document_normalization import normalize_raw_event
                yield ("documents.enrichment-requested.v1", normalize_raw_event(event).model_dump(mode="json"))
            else:
                yield (self.topic, event.model_dump(mode="json"))
        except httpx.HTTPError:  # Registry outages must restart, never DLQ data.
            raise
        except Exception as exc:
            yield (dlq_topic(self.topic), _dead_letter(self.topic, value, exc))


class EventTimestampAssigner(TimestampAssigner):
    def extract_timestamp(self, event, _record_timestamp):
        return event_timestamp(event)


class ContentDeduplicator(KeyedProcessFunction):
    def open(self, runtime_context):
        from pyflink.common import Time, Types
        from pyflink.datastream.state import StateTtlConfig, ValueStateDescriptor
        descriptor = ValueStateDescriptor("b4-content-state-v1", Types.PICKLED_BYTE_ARRAY())
        descriptor.enable_time_to_live(StateTtlConfig.new_builder(Time.days(14))
                                       .set_update_type(StateTtlConfig.UpdateType.OnCreateAndWrite)
                                       .set_state_visibility(StateTtlConfig.StateVisibility.NeverReturnExpired).build())
        self.state = runtime_context.get_state(descriptor)

    def process_element(self, event, ctx):
        from models.events import dlq_topic
        engine = self.state.value() or StreamIntelligenceEngine(phrase_key="")
        try:
            output = engine.process_enriched(event, watermark=_watermark(ctx))
        except Exception as exc:
            yield (dlq_topic("documents.enriched.v1"), _dead_letter("documents.enriched.v1", event, exc))
            return
        self.state.update(engine)
        for processed in output.processed:
            yield ("documents.processed.v1", processed)
            if processed["payload"]["processing"]["duplicate_of_document_id"] is None:
                yield ("b4.unique", event)
        for late in output.late:
            yield ("documents.late.v1", late)


class PhraseWindows(KeyedProcessFunction):
    def open(self, runtime_context):
        from pyflink.common import Types
        from pyflink.datastream.state import ValueStateDescriptor
        self.state = runtime_context.get_state(ValueStateDescriptor("b4-phrase-state-v1", Types.PICKLED_BYTE_ARRAY()))

    def process_element(self, item, ctx):
        phrase, event = item
        engine = self.state.value() or StreamIntelligenceEngine(phrase_key=phrase)
        output = engine.process_enriched(event, watermark=_watermark(ctx))
        self.state.update(engine)
        interval = 15 * 60 * 1000
        timestamp = event_timestamp(event)
        ctx.timer_service().register_event_time_timer((timestamp // interval + 1) * interval)
        # Quiet-input evaluation publishes provisional panes. Late data revises
        # these same event-time windows; processing timers also renew health.
        clock = ctx.timer_service().current_processing_time()
        ctx.timer_service().register_processing_time_timer((clock // interval + 1) * interval)
        for event in output.signals:
            yield ("signals.detected.v1", event)
        for heartbeat in output.heartbeats:
            yield ("pipeline.evaluated.v1", heartbeat)

    def on_timer(self, timestamp, ctx):
        engine = self.state.value()
        if engine is None:
            return
        output = engine.evaluate(datetime.fromtimestamp(timestamp / 1000, timezone.utc), watermark=_watermark(ctx))
        self.state.update(engine)
        for event in output.signals:
            yield ("signals.detected.v1", event)
        for heartbeat in output.heartbeats:
            yield ("pipeline.evaluated.v1", heartbeat)
        from pyflink.datastream.time_domain import TimeDomain
        if ctx.time_domain() == TimeDomain.PROCESSING_TIME:
            ctx.timer_service().register_processing_time_timer(timestamp + 15 * 60 * 1000)


def _phrase_records(event):
    for phrase in sorted({item["phrase"].strip().lower() for item in event["payload"]["enrichment"]["canonical_phrases"]}):
        if phrase:
            yield phrase, event


class EncodeKafkaRecord(MapFunction):
    def __init__(self, topic: str):
        self.topic = topic

    def open(self, _context):
        from services.kafka_runtime import SchemaRegistry
        self.adapter = ConfluentJsonAdapter(registry=SchemaRegistry())

    def map(self, item):
        from pyflink.common import Row
        return Row(bytearray(item[1]["partition_key"].encode()), bytearray(self.adapter.encode(self.topic, item[1])))


def _sink(stream, topic, brokers):
    from pyflink.common import SerializationSchema, Types
    from pyflink.datastream.connectors.base import DeliveryGuarantee
    from pyflink.datastream.connectors.kafka import KafkaRecordSerializationSchema, KafkaSink
    from pyflink.java_gateway import get_gateway
    from services.kafka_runtime import physical_topic
    jvm = get_gateway().jvm
    serde = (KafkaRecordSerializationSchema.builder().set_topic(physical_topic(topic))
             .set_key_serialization_schema(SerializationSchema(jvm.rhetoriq.KafkaRowSchemas.Key()))
             .set_value_serialization_schema(SerializationSchema(jvm.rhetoriq.KafkaRowSchemas.Value())).build())
    sink = (KafkaSink.builder().set_bootstrap_servers(brokers).set_record_serializer(serde)
            .set_delivery_guarantee(DeliveryGuarantee.EXACTLY_ONCE)
            .set_transactional_id_prefix(f"{os.getenv('FLINK_TRANSACTION_PREFIX', 'rhetoriq-b4')}-{physical_topic(topic)}-")
            .set_property("transaction.timeout.ms", "900000").build())
    byte_type = Types.PRIMITIVE_ARRAY(Types.BYTE())
    (stream.filter(lambda item: item[0] == topic).map(EncodeKafkaRecord(topic), output_type=Types.ROW([byte_type, byte_type]))
     .sink_to(sink).name(f"kafka-{topic}").uid(f"b4-sink-{topic}"))


def main():
    from pyflink.common import Configuration, Duration, Types, WatermarkStrategy
    from pyflink.common import DeserializationSchema
    from pyflink.java_gateway import get_gateway
    from pyflink.datastream import StreamExecutionEnvironment
    from pyflink.datastream.checkpoint_config import ExternalizedCheckpointRetention
    from pyflink.datastream.connectors.kafka import KafkaOffsetResetStrategy, KafkaOffsetsInitializer, KafkaSource
    from models.events import dlq_topic
    from services.kafka_runtime import SchemaRegistry, physical_topic
    brokers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "broker:19092")
    outputs = ("documents.enrichment-requested.v1", "documents.processed.v1", "signals.detected.v1",
               "documents.late.v1", "pipeline.evaluated.v1", dlq_topic("raw.documents.v1"), dlq_topic("documents.enriched.v1"))
    registry = SchemaRegistry()
    for topic in (*outputs, "raw.documents.v1", "documents.enriched.v1"):
        registry.ensure_schema(topic)
    runtime_configuration = Configuration()
    runtime_configuration.set_string(
        "execution.checkpointing.dir",
        os.getenv("FLINK_CHECKPOINT_DIR", "file:///opt/flink/checkpoints"),
    )
    env = StreamExecutionEnvironment.get_execution_environment(runtime_configuration)
    env.set_parallelism(2)
    env.enable_checkpointing(60_000)
    env.get_checkpoint_config().set_externalized_checkpoint_retention(
        ExternalizedCheckpointRetention.RETAIN_ON_CANCELLATION
    )

    def source(topic, group):
        kafka_source = (KafkaSource.builder().set_bootstrap_servers(brokers).set_topics(physical_topic(topic))
                        .set_group_id(group).set_starting_offsets(KafkaOffsetsInitializer.committed_offsets(KafkaOffsetResetStrategy.EARLIEST))
                        .set_value_only_deserializer(DeserializationSchema(get_gateway().jvm.rhetoriq.KafkaRowSchemas.Bytes()))
                        .set_property("isolation.level", "read_committed")
                        .set_property("commit.offsets.on.checkpoint", "true").build())
        return (env.from_source(kafka_source, WatermarkStrategy.no_watermarks(), f"kafka-{topic}")
                .flat_map(DecodeAndNormalize(topic), output_type=Types.PICKLED_BYTE_ARRAY()).uid(f"b4-source-{topic}"))

    group_prefix = os.getenv("FLINK_SOURCE_GROUP_PREFIX", "rhetoriq-flink")
    raw = source("raw.documents.v1", f"{group_prefix}-normalizer-v1")
    enriched_source = source("documents.enriched.v1", f"{group_prefix}-intelligence-v1")
    enriched = (enriched_source.filter(lambda item: item[0] == "documents.enriched.v1")
                .map(lambda item: item[1], output_type=Types.PICKLED_BYTE_ARRAY())
                .assign_timestamps_and_watermarks(WatermarkStrategy.for_bounded_out_of_orderness(Duration.of_minutes(15))
                                                  .with_timestamp_assigner(EventTimestampAssigner()).with_idleness(Duration.of_minutes(1))))
    content = (enriched.key_by(lambda event: hashlib.sha256(event["payload"]["document"]["text"].encode()).hexdigest(), Types.STRING())
               .process(ContentDeduplicator(), output_type=Types.PICKLED_BYTE_ARRAY()).uid("b4-content-dedup-v1"))
    phrases = (content.filter(lambda item: item[0] == "b4.unique").flat_map(lambda item: _phrase_records(item[1]), Types.PICKLED_BYTE_ARRAY())
               .key_by(lambda item: item[0], Types.STRING()).process(PhraseWindows(), Types.PICKLED_BYTE_ARRAY()).uid("b4-phrase-windows-v1"))
    errors = enriched_source.filter(lambda item: item[0] != "documents.enriched.v1")
    stream = raw.union(content.filter(lambda item: item[0] != "b4.unique"), phrases, errors)
    for topic in outputs:
        _sink(stream, topic, brokers)
    env.execute("rhetoriq-b4-flink")


if __name__ == "__main__":
    main()
