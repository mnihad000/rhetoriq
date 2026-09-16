"""Legacy command name for the Kafka-backed investigation worker.

This module intentionally never scans the database for queued work. Production
dispatch is exclusively driven by ``investigations.requested.v1``.
"""

from events.worker import KafkaEventWorker


def main() -> None:
    KafkaEventWorker(role="investigations").run_forever()


if __name__ == "__main__":
    main()
