"""Orquestación de un corte."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import psycopg

from . import database, scd2
from .config import MAX_DELETE_RATIO, MAX_REJECT_RATIO
from .database import QUARANTINE_COLUMNS, QUARANTINE_TYPES, STAGING_TYPES
from .extract import checksum, read_batches
from .transform import STAGING_COLUMNS, normalize, validate

log = logging.getLogger(__name__)

RUN_LOG_SQL = """
INSERT INTO etl_run (snapshot_date, source_file, source_checksum, rows_read,
                     rows_valid, rows_rejected, n_insert, n_update, n_delete,
                     n_unchanged)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


class RejectRatioExceeded(RuntimeError):
    """Demasiadas filas rechazadas: apunta a cambio de esquema o corrupción."""


class OutOfOrderSnapshot(RuntimeError):
    """Se intenta aplicar un corte anterior al último ya historificado."""


def _should_skip(conn: psycopg.Connection, snapshot: date, digest: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT run_id, source_checksum FROM etl_run "
                    "WHERE snapshot_date = %s", (snapshot,))
        previous = cur.fetchone()
        if previous:
            if previous[1] == digest:
                log.info("Corte %s ya aplicado con el mismo archivo (run_id=%s); "
                         "se omite", snapshot, previous[0])
                return True
            # Reprocesar un corte cerrado exige revertir versiones SCD2
            raise ValueError(
                f"el corte {snapshot} ya se procesó (run_id={previous[0]}) pero "
                "con otro archivo"
            )

        cur.execute("SELECT max(valid_from) FROM movimientos_hist")
        last = cur.fetchone()[0]
        if last and snapshot <= last:
            raise OutOfOrderSnapshot(
                f"se intenta cargar {snapshot} cuando el histórico ya llega a {last}"
            )
    return False


def process_snapshot(conn: psycopg.Connection, path: Path, snapshot: date) -> None:
    digest = checksum(path)
    if _should_skip(conn, snapshot, digest):
        conn.rollback()
        return

    with conn.cursor() as cur:
        cur.execute("TRUNCATE movimientos_stg")

    rows_read = rows_valid = rows_rejected = 0
    hits: dict[str, int] = {}

    for raw in read_batches(path):
        result = validate(normalize(raw), raw)

        database.copy_into(conn, "movimientos_stg", STAGING_COLUMNS,
                           STAGING_TYPES, database.to_rows(result.valid))
        database.copy_into(conn, "quarantine", QUARANTINE_COLUMNS, QUARANTINE_TYPES,
                           [(path.name, reasons, payload)
                            for reasons, payload in database.to_rows(result.rejected)],
                           binary=False)

        rows_read += len(raw)
        rows_valid += len(result.valid)
        rows_rejected += len(result.rejected)
        for code, count in result.rule_hits.items():
            hits[code] = hits.get(code, 0) + count

    if rows_read == 0:
        raise RejectRatioExceeded(f"{path.name} no contiene filas")

    ratio = rows_rejected / rows_read
    if ratio > MAX_REJECT_RATIO:
        raise RejectRatioExceeded(
            f"{ratio:.1%} de las filas fueron rechazadas "
            f"(umbral {MAX_REJECT_RATIO:.0%})"
        )

    stats = scd2.merge(conn, snapshot, path.name, MAX_DELETE_RATIO)

    with conn.cursor() as cur:
        cur.execute(RUN_LOG_SQL, (snapshot, path.name, digest, rows_read, rows_valid,
                                  rows_rejected, stats.inserts, stats.updates,
                                  stats.deletes, stats.unchanged))
    conn.commit()

    log.info("Corte %s | leídas=%d válidas=%d cuarentena=%d | altas=%d "
             "correcciones=%d bajas=%d sin_cambio=%d",
             snapshot, rows_read, rows_valid, rows_rejected,
             stats.inserts, stats.updates, stats.deletes, stats.unchanged)
    for code, count in sorted(hits.items(), key=lambda kv: -kv[1]):
        log.info("  calidad: %-24s %d filas", code, count)
