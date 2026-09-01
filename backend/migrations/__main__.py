from config import get_settings
from migrations.runner import run_migrations


def main() -> None:
    settings = get_settings()
    if not settings.DATABASE_URL:
        raise SystemExit("DATABASE_URL is required to run PostgreSQL migrations.")
    run_migrations(settings.DATABASE_URL)


if __name__ == "__main__":
    main()
