# Flink's official 2.3.0 distribution is the cluster runtime.  PyFlink is
# copied from the official Python 3.12 image so the API container's 3.13
# runtime cannot leak into the job image.
FROM python:3.12-slim-bookworm AS pyflink
COPY infra/flink/requirements.txt /tmp/flink-requirements.txt
RUN python -m pip install --no-cache-dir apache-flink==2.3.0 -r /tmp/flink-requirements.txt

FROM maven:3.9.10-eclipse-temurin-21 AS kafka-connector
ARG KAFKA_CONNECTOR_SOURCE_TAG=v4.0.1
WORKDIR /build
# Apache has no published 2.3 Kafka connector. Compile the pinned source
# against the exact runtime instead of assuming a 2.0 binary is compatible.
# This candidate still requires the real cluster/recovery qualification gate.
RUN curl -fsSL "https://codeload.github.com/apache/flink-connector-kafka/tar.gz/refs/tags/${KAFKA_CONNECTOR_SOURCE_TAG}" -o kafka-source.tgz \
    && mkdir source && tar -xzf kafka-source.tgz -C source --strip-components=1 \
    && cd source \
    && mvn -B -pl flink-sql-connector-kafka -am -Dflink.version=2.3.0 \
       -DskipTests -Dcheckstyle.skip -Dspotless.check.skip=true package \
    && cp flink-sql-connector-kafka/target/flink-sql-connector-kafka-4.0.1-2.0.jar /build/b4-kafka-4.0.1-flink-2.3.0.jar

FROM flink:2.3.0-scala_2.12-java21
COPY --from=pyflink /usr/local /usr/local
ENV PATH="/usr/local/bin:${PATH}" \
    PYTHONPATH="/opt/flink/usrlib/backend"

COPY --from=kafka-connector /build/b4-kafka-4.0.1-flink-2.3.0.jar /opt/flink/lib/

COPY backend /opt/flink/usrlib/backend
COPY infra/flink/connector-compatibility.txt /opt/flink/usrlib/connector-compatibility.txt
COPY infra/flink/application-entrypoint.sh /opt/flink/application-entrypoint.sh
USER root
COPY infra/flink/java /tmp/b4-java
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 libffi8 libsqlite3-0 libssl3 libbz2-1.0 liblzma5 libreadline8 libncursesw6 curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/flink/checkpoints /opt/flink/savepoints /tmp/b4-classes \
    && javac -cp '/opt/flink/lib/*' -d /tmp/b4-classes /tmp/b4-java/rhetoriq/KafkaRowSchemas.java \
    && jar cf /opt/flink/lib/b4-kafka-row-schemas.jar -C /tmp/b4-classes . \
    && FLINK_PYTHON_JAR=$(find /usr/local/lib/python3.12/site-packages/pyflink/lib -name 'flink-python*.jar' -print -quit) \
    && test -n "$FLINK_PYTHON_JAR" \
    && cp "$FLINK_PYTHON_JAR" /opt/flink/lib/ \
    && chown -R flink:flink /opt/flink/checkpoints /opt/flink/savepoints /opt/flink/usrlib \
    && chmod +x /opt/flink/application-entrypoint.sh \
    && python --version && python -c "import pyflink,pydantic,httpx,jsonschema; import flink.job"
USER flink
ENTRYPOINT ["/opt/flink/application-entrypoint.sh"]
