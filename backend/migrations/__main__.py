import argparse

from config import get_settings
from migrations.runner import run_migrations, verify_migrations


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply or verify PostgreSQL migrations")
    parser.add_argument("--check", action="store_true", help="Verify without changing the database")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.DATABASE_URL:
        raise SystemExit("DATABASE_URL is required to run PostgreSQL migrations.")
    if args.check:
        verify_migrations(settings.DATABASE_URL)
    else:
        run_migrations(settings.DATABASE_URL, mode="apply")


if __name__ == "__main__":
    main()
