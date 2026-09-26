from services.database import is_postgres_database, postgres_query


def test_postgres_target_detection_accepts_urls_and_libpq_conninfo() -> None:
    assert is_postgres_database("postgresql://user:secret@example.test/app")
    assert is_postgres_database(
        "user=rhetoriq dbname=rhetoriq_test host=127.0.0.1 port=5432 "
        "options='-c search_path=isolated'"
    )
    assert is_postgres_database("dbname=rhetoriq_test")
    assert not is_postgres_database(":memory:")
    assert not is_postgres_database("var/data/investigations.db")
    assert not is_postgres_database("not_a_libpq_option=value")


def test_postgres_query_converts_qmarks_and_preserves_literal_percent() -> None:
    assert postgres_query("SELECT * FROM runs WHERE id=? AND name LIKE 'inv_%'") == (
        "SELECT * FROM runs WHERE id=%s AND name LIKE 'inv_%%'"
    )
