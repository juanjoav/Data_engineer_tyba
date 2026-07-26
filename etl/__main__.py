"""Punto de entrada:  python -m etl"""
from __future__ import annotations

import logging
import sys

from . import database, report
from .config import BASE_DATE, DATA_DIR, LOG_LEVEL
from .extract import discover
from .pipeline import process_snapshot

log = logging.getLogger("etl")


def main() -> int:
    logging.basicConfig(level=LOG_LEVEL, stream=sys.stdout,
                        format="%(asctime)s | %(levelname)-7s | %(message)s")

    snapshots = discover(DATA_DIR, BASE_DATE)
    if not snapshots:
        log.error("No hay archivos *_dia_T<n>.parquet en %s", DATA_DIR)
        return 1

    conn = database.connect()
    try:
        for path, snapshot in snapshots:
            process_snapshot(conn, path, snapshot)
    except Exception as exc:
        conn.rollback()
        log.error("El corte fue rechazado, no se aplicó nada: %s", exc)
        conn.close()
        return 1

    try:
        ok = report.check_invariants(conn)
        report.print_summary(conn)
    finally:
        conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
