from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2 import OperationalError, sql
from psycopg2.extensions import connection as PsycopgConnection

from app.data.databases.config import get_postgres_settings


def get_connection(*, autocommit: bool = False) -> PsycopgConnection:
    settings = get_postgres_settings()
    connection = psycopg2.connect(**settings.as_connect_kwargs())
    connection.autocommit = autocommit
    return connection


@contextmanager
def managed_connection(*, autocommit: bool = False) -> Iterator[PsycopgConnection]:
    connection = get_connection(autocommit=autocommit)
    try:
        yield connection
        if not autocommit:
            connection.commit()
    except Exception:
        if not autocommit:
            connection.rollback()
        raise
    finally:
        connection.close()


def ensure_database_exists() -> None:
    settings = get_postgres_settings()
    connect_kwargs = settings.as_connect_kwargs()
    target_db = str(connect_kwargs["dbname"])

    maintenance_kwargs = dict(connect_kwargs)
    maintenance_kwargs.pop("dbname", None)

    for maintenance_db in ("postgres", "template1"):
        maintenance_kwargs["dbname"] = maintenance_db
        try:
            connection = psycopg2.connect(**maintenance_kwargs)
            try:
                connection.autocommit = True
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (target_db,))
                    if cursor.fetchone():
                        return
                    cursor.execute(
                        sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db))
                    )
                    return
            finally:
                connection.close()
        except OperationalError:
            continue

    raise RuntimeError(
        f"Unable to verify or create target database '{target_db}'. "
        "Check host/port credentials and maintenance database access."
    )
