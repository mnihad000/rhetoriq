# Flink's official 2.3.0 distribution is the cluster runtime.  PyFlink is
# copied from the official Python 3.12 image so the API container's 3.13
# runtime cannot leak into the job image.
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS pyflink
COPY infra/flink/requirements.txt /tmp/flink-requirements.txt
RUN python -m pip install --no-cache-dir apache-flink==2.3.0 -r /tmp/flink-requirements.txt

FROM maven:3.9.10-eclipse-temurin-21@sha256:193ed19a4806b4bbad65b2e8a2a9bbf901b6e216eb0d5761c70c2c4537cd53d7 AS kafka-connector
ARG KAFKA_CONNECTOR_SOURCE_COMMIT=9e4bd3c04f57e5c2189b5d5ddae521518c52cffa
ARG KAFKA_CONNECTOR_SOURCE_SHA256=029736e2a41f9c4ab3953e5acb7b9f51014caf6b77bc541f68656ef23796ca5c
WORKDIR /build
COPY infra/flink/patches/KafkaDataTypeUtils.java /tmp/KafkaDataTypeUtils.java
# Apache has no published 2.3 Kafka connector. Compile the pinned source
# against the exact runtime instead of assuming a 2.0 binary is compatible.
# This candidate still requires the real cluster/recovery qualification gate.
RUN --mount=type=cache,target=/root/.m2/repository \
    curl -fsSL "https://codeload.github.com/apache/flink-connector-kafka/tar.gz/${KAFKA_CONNECTOR_SOURCE_COMMIT}" -o kafka-source.tgz \
    && echo "${KAFKA_CONNECTOR_SOURCE_SHA256}  kafka-source.tgz" | sha256sum -c - \
    && mkdir source && tar -xzf kafka-source.tgz -C source --strip-components=1 \
    && cd source \
    && cp /tmp/KafkaDataTypeUtils.java \
       flink-connector-kafka/src/main/java/org/apache/flink/streaming/connectors/kafka/table/KafkaDataTypeUtils.java \
    && sed -i '/import org.apache.flink.table.types.utils.DataTypeUtils;/d' \
       flink-connector-kafka/src/main/java/org/apache/flink/streaming/connectors/kafka/table/KafkaDynamicSource.java \
       flink-connector-kafka/src/main/java/org/apache/flink/streaming/connectors/kafka/table/KafkaDynamicSink.java \
    && sed -i 's/DataTypeUtils\.stripRowPrefix/KafkaDataTypeUtils.stripRowPrefix/g' \
       flink-connector-kafka/src/main/java/org/apache/flink/streaming/connectors/kafka/table/KafkaDynamicSource.java \
       flink-connector-kafka/src/main/java/org/apache/flink/streaming/connectors/kafka/table/KafkaDynamicSink.java \
    && mvn -B -pl flink-sql-connector-kafka -am -Dflink.version=2.3.0 \
       -Dmaven.test.skip=true -Dcheckstyle.skip -Dspotless.check.skip=true package \
    && cp flink-sql-connector-kafka/target/flink-sql-connector-kafka-4.0.1.jar /build/b4-kafka-4.0.1-flink-2.3.0.jar

FROM flink:2.3.0-scala_2.12-java21@sha256:b16d828430dc056051d9aa25b825560ffa16cd9f7f2e37013ad66be5fe555147 AS row-schemas
USER root
COPY infra/flink/java /tmp/b4-java
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-21-jdk-headless \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/b4-classes \
    && javac -cp '/opt/flink/lib/*' -d /tmp/b4-classes /tmp/b4-java/rhetoriq/KafkaRowSchemas.java \
    && jar cf /tmp/b4-kafka-row-schemas.jar -C /tmp/b4-classes .

FROM flink:2.3.0-scala_2.12-java21@sha256:b16d828430dc056051d9aa25b825560ffa16cd9f7f2e37013ad66be5fe555147
COPY --from=pyflink /usr/local /usr/local
ENV PATH="/usr/local/bin:${PATH}" \
    PYTHONPATH="/opt/flink/usrlib/backend"

COPY --from=kafka-connector /build/b4-kafka-4.0.1-flink-2.3.0.jar /opt/flink/lib/
COPY --from=row-schemas /tmp/b4-kafka-row-schemas.jar /opt/flink/lib/

COPY backend /opt/flink/usrlib/backend
COPY infra/flink/connector-compatibility.txt /opt/flink/usrlib/connector-compatibility.txt
COPY infra/flink/application-entrypoint.sh /opt/flink/application-entrypoint.sh
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 libffi8 libsqlite3-0 libssl3 libbz2-1.0 liblzma5 libreadline8 libncursesw6 curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/flink/checkpoints /opt/flink/savepoints \
    && FLINK_PYTHON_JAR=$(find /usr/local/lib/python3.12/site-packages/pyflink/opt -name 'flink-python*.jar' -print -quit) \
    && test -n "$FLINK_PYTHON_JAR" \
    && cp "$FLINK_PYTHON_JAR" /opt/flink/lib/ \
    && chown -R flink:flink /opt/flink/checkpoints /opt/flink/savepoints /opt/flink/usrlib \
    && sed -i 's/\r$//' /opt/flink/application-entrypoint.sh \
    && chmod +x /opt/flink/application-entrypoint.sh \
    && python --version \
    && python -c "import pyflink,pydantic,httpx,jsonschema; from pyflink.common import Configuration,Time; from pyflink.datastream.checkpoint_config import CheckpointConfig,ExternalizedCheckpointRetention; from pyflink.datastream.state import StateTtlConfig; assert hasattr(CheckpointConfig,'set_externalized_checkpoint_retention'); assert StateTtlConfig.new_builder(Time.days(14)).build().get_ttl().to_milliseconds()==1209600000; config=Configuration(); config.set_string('execution.checkpointing.dir','file:///tmp/checkpoints'); assert config.get_string('execution.checkpointing.dir',None)=='file:///tmp/checkpoints'; import flink.job"
USER flink
ENTRYPOINT ["/opt/flink/application-entrypoint.sh"]
