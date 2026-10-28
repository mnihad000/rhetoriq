"""Command-line entrypoint for the database-backed investigation worker."""

from events.worker import DatabaseEventWorker


def main() -> None:
    DatabaseEventWorker().run_forever()


if __name__ == "__main__":
    main()
