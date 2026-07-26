"""Acceso a PostgreSQL: conexión y carga masiva."""
from __future__ import annotations

import logging
import time
from typing import Sequence

import numpy as np
import pandas as pd
import psycopg

from .config import DSN

log = logging.getLogger(__name__)

STAGING_TYPES: list[str] = [
    "text", "date", "text", "text", "text", "float8", "text", "text", "text[]",
]
QUARANTINE_COLUMNS: tuple[str, ...] = ("source_file", "reject_reasons", "raw_payload")
QUARANTINE_TYPES: list[str] = ["text", "text[]", "jsonb"]


def connect(retries: int = 15, delay: float = 2.0) -> psycopg.Connection:
    """El `depends_on: service_healthy` reduce la ventana de carrera pero no la
    elimina; reintentar es más honesto que un `sleep 15` a ciegas."""
    for attempt in range(1, retries + 1):
        try:
            return psycopg.connect(DSN, autocommit=False)
        except psycopg.OperationalError:
            if attempt == retries:
                raise
            log.warning("Esperando a PostgreSQL (%d/%d)...", attempt, retries)
            time.sleep(delay)
    raise RuntimeError("inalcanzable")


def to_rows(df: pd.DataFrame) -> list[tuple]:
    """DataFrame -> tuplas nativas.

    Se convierte POR COLUMNA (`Series.tolist()` corre en C) y no fila a fila:
    psycopg no entiende `pd.NA` ni los dtypes nullable de pandas, y hacerlo
    fila a fila sería el cuello de botella real del pipeline.
    """
    columns: list[list] = []
    for name in df.columns:
        series = df[name]
        values = series.tolist()
        if series.dtype == object or isinstance(series.dtype, pd.StringDtype):
            values = [
                None if v is None or v is pd.NA
                or (isinstance(v, float) and np.isnan(v)) else v
                for v in values
            ]
        columns.append(values)
    return list(zip(*columns))


def copy_into(conn: psycopg.Connection, table: str, columns: Sequence[str],
              types: list[str], rows: Sequence[tuple], binary: bool = True) -> int:
    """Carga masiva con COPY: ~300k-1M filas/s frente a 5-10k de `to_sql`.

    El formato binario no sirve para JSONB (la cuarentena), que va en texto.
    """
    if not rows:
        return 0
    fmt = " (FORMAT BINARY)" if binary else ""
    statement = f"COPY {table} ({', '.join(columns)}) FROM STDIN{fmt}"
    with conn.cursor() as cur, cur.copy(statement) as copy:
        copy.set_types(types)
        for row in rows:
            copy.write_row(row)
    return len(rows)
