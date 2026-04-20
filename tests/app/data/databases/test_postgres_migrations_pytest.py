from __future__ import annotations

import pytest

from app.data.databases import migrate
from app.data.databases.postgres import ensure_database_exists, managed_connection


pytestmark = pytest.mark.postgres_live


@pytest.fixture(autouse=True)
def reset_and_migrate_db() -> None:
    ensure_database_exists()
    with managed_connection() as connection:
        migrate.drop_managed_schemas(connection)
        migrate.apply_migrations(connection)


def test_migration_inventory_matches_applied_rows_pytest() -> None:
    expected_ids = migrate.list_migrations()
    with managed_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT migration_id
                FROM core.schema_migrations
                ORDER BY migration_id ASC;
                """
            )
            applied_ids = [str(row[0]) for row in cursor.fetchall()]

    assert applied_ids == expected_ids


def test_apply_migrations_is_idempotent_pytest() -> None:
    with managed_connection() as connection:
        applied_now = migrate.apply_migrations(connection)

    assert applied_now == []


def test_expected_analysis_tables_exist_after_migration_pytest() -> None:
    expected_tables = {
        ("core", "schema_migrations"),
        ("core", "providers"),
        ("catalog", "events"),
        ("nba", "nba_games"),
        ("nba", "nba_analysis_game_team_profiles"),
        ("nba", "nba_analysis_state_panel"),
    }
    with managed_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND (table_schema, table_name) IN (
                    ('core', 'schema_migrations'),
                    ('core', 'providers'),
                    ('catalog', 'events'),
                    ('nba', 'nba_games'),
                    ('nba', 'nba_analysis_game_team_profiles'),
                    ('nba', 'nba_analysis_state_panel')
                  );
                """
            )
            actual_tables = {(str(schema_name), str(table_name)) for schema_name, table_name in cursor.fetchall()}

    assert actual_tables == expected_tables
