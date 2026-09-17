package rhetoriq;
import org.apache.flink.api.common.serialization.SerializationSchema;
import org.apache.flink.api.common.serialization.DeserializationSchema;
import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.types.Row;

/** Byte keys and Confluent-framed values without Python callbacks in Kafka. */
public final class KafkaRowSchemas {
    public static final class Bytes implements DeserializationSchema<byte[]> {
        @Override public byte[] deserialize(byte[] message) { return message; }
        @Override public boolean isEndOfStream(byte[] message) { return false; }
        @Override public TypeInformation<byte[]> getProducedType() { return TypeInformation.of(byte[].class); }
    }
    public static final class Key implements SerializationSchema<Row> {
        @Override public byte[] serialize(Row row) { return (byte[]) row.getField(0); }
    }
    public static final class Value implements SerializationSchema<Row> {
        @Override public byte[] serialize(Row row) { return (byte[]) row.getField(1); }
    }
}
