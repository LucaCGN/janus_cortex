from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.data.databases import migrate
from app.data.databases.postgres import ensure_database_exists, managed_connection
from app.data.databases.repositories import JanusUpsertRepository


pytestmark = pytest.mark.postgres_live


@pytest.fixture(autouse=True)
def reset_and_migrate_db() -> None:
    ensure_database_exists()
    with managed_connection() as connection:
        migrate.drop_managed_schemas(connection)
        migrate.apply_migrations(connection)


def test_upsert_repository_updates_existing_rows_pytest() -> None:
    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        provider_id = repo.upsert_provider(
            provider_id=str(uuid4()),
            code="pytest_polymarket",
            name="Polymarket",
            category="prediction_market",
        )
        provider_id_second = repo.upsert_provider(
            provider_id=str(uuid4()),
            code="pytest_polymarket",
            name="Polymarket Updated",
            category="prediction_market",
        )
        connection.commit()

        with connection.cursor() as cursor:
            cursor.execute("SELECT provider_id, name FROM core.providers WHERE code = %s;", ("pytest_polymarket",))
            row = cursor.fetchone()

    assert provider_id_second == provider_id
    assert str(row[0]) == provider_id
    assert str(row[1]) == "Polymarket Updated"


def test_upsert_repository_preserves_market_and_tick_uniqueness_pytest() -> None:
    ts = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        provider_id = repo.upsert_provider(
            provider_id=str(uuid4()),
            code="pytest_provider_ticks",
            name="Polymarket",
            category="prediction_market",
        )
        event_type_id = repo.upsert_event_type(
            event_type_id=str(uuid4()),
            code="pytest_nba_game",
            name="NBA Game",
            domain="sports",
        )
        event_id = repo.upsert_event(
            event_id=str(uuid4()),
            event_type_id=event_type_id,
            information_profile_id=None,
            title="Celtics vs Lakers",
            status="resolved",
            canonical_slug="pytest-celtics-lakers",
        )
        market_id = repo.upsert_market(
            market_id=str(uuid4()),
            event_id=event_id,
            question="Who wins?",
            market_type="moneyline",
            market_slug="pytest-celtics-lakers-moneyline",
            settlement_status="resolved",
        )
        _ = repo.upsert_market_external_ref(
            market_ref_id=str(uuid4()),
            market_id=market_id,
            provider_id=provider_id,
            external_market_id="pytest-market-001",
        )
        outcome_id = repo.upsert_outcome(
            outcome_id=str(uuid4()),
            market_id=market_id,
            outcome_index=0,
            outcome_label="BOS",
            token_id="pytest-token-bos",
            is_winner=True,
        )
        inserted_first = repo.insert_outcome_price_tick(
            outcome_id=outcome_id,
            ts=ts,
            source="clob_prices_history",
            price=0.61,
            ignore_duplicates=True,
        )
        inserted_second = repo.insert_outcome_price_tick(
            outcome_id=outcome_id,
            ts=ts,
            source="clob_prices_history",
            price=0.61,
            ignore_duplicates=True,
        )
        connection.commit()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*)
                FROM market_data.outcome_price_ticks
                WHERE outcome_id = %s AND source = %s;
                """,
                (outcome_id, "clob_prices_history"),
            )
            tick_count = int(cursor.fetchone()[0])

    assert inserted_first is True
    assert inserted_second is False
    assert tick_count == 1
