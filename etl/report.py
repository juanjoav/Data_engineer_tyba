"""Validación de invariantes y resumen de cierre."""
from __future__ import annotations

import logging

import psycopg

from .config import SQL_DIR

log = logging.getLogger(__name__)

QUERIES: tuple[tuple[str, str], ...] = (
    ("Bitácora de ejecuciones", """
        SELECT run_id, snapshot_date, source_file, rows_read, rows_valid,
               rows_rejected, n_insert, n_update, n_delete, n_unchanged
        FROM etl_run ORDER BY run_id"""),
    ("Estado del histórico", """
        SELECT count(*) FILTER (WHERE is_current AND NOT is_deleted) AS vigentes,
               count(*) FILTER (WHERE is_current AND is_deleted)     AS lapidas,
               count(*) FILTER (WHERE NOT is_current)                AS cerradas,
               count(*)                                              AS versiones
        FROM movimientos_hist"""),
    ("Cuarentena por motivo", """
        SELECT unnest(reject_reasons) AS motivo, count(*) AS filas
        FROM quarantine GROUP BY 1 ORDER BY 2 DESC"""),
    ("Ejemplo de un movimiento corregido", """
        SELECT version_num, operation, valid_from, valid_to, is_current,
               id_cliente, amount, description
        FROM movimientos_hist
        WHERE business_key = (SELECT business_key FROM movimientos_hist
                              WHERE operation = 'UPDATE' LIMIT 1)
        ORDER BY version_num"""),
)


def check_invariants(conn: psycopg.Connection) -> bool:
    """Un histórico corrupto es peor que un pipeline caído: el caído se nota."""
    conn.add_notice_handler(lambda diag: log.info("%s", diag.message_primary))
    try:
        with conn.cursor() as cur:
            cur.execute((SQL_DIR / "validate.sql").read_text(encoding="utf-8"))
        conn.commit()
        return True
    except psycopg.errors.AssertFailure as exc:
        conn.rollback()
        log.error("FALLO DE INTEGRIDAD: %s", exc.diag.message_primary)
        return False


def _render(title: str, columns: list[str], rows: list[tuple]) -> str:
    if not rows:
        return f"\n### {title}\n(sin filas)"
    widths = [max(len(str(c)), *(len(str(r[i])) for r in rows))
              for i, c in enumerate(columns)]
    header = " | ".join(str(c).ljust(w) for c, w in zip(columns, widths))
    divider = "-+-".join("-" * w for w in widths)
    body = "\n".join(" | ".join(str(v).ljust(w) for v, w in zip(row, widths))
                     for row in rows)
    return f"\n### {title}\n{header}\n{divider}\n{body}"


def print_summary(conn: psycopg.Connection) -> None:
    for title, query in QUERIES:
        with conn.cursor() as cur:
            cur.execute(query)
            columns = [d.name for d in cur.description]
            print(_render(title, columns, cur.fetchall()))
    print()
