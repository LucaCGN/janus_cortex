from __future__ import annotations

from collections.abc import Iterator

from psycopg2.extensions import connection as PsycopgConnection

from app.data.databases.postgres import managed_connection


def get_db_connection() -> Iterator[PsycopgConnection]:
    with managed_connection() as connection:
        yield connection
