"""Historificación SCD Tipo 2.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import psycopg

from .config import ATTRIBUTES, BUSINESS_KEY

log = logging.getLogger(__name__)

HASH_SEPARATOR = r"E'\x1f'"
NULL_SENTINEL = "@@NULL@@"


class DeleteRatioExceeded(RuntimeError):
    """El corte daría de baja una fracción implausible del universo vigente."""


@dataclass(frozen=True)
class MergeStats:
    inserts: int = 0
    updates: int = 0
    deletes: int = 0
    unchanged: int = 0

    @classmethod
    def from_counts(cls, counts: dict[str, int]) -> "MergeStats":
        return cls(counts.get("INSERT", 0), counts.get("UPDATE", 0),
                   counts.get("DELETE", 0), counts.get("UNCHANGED", 0))


def hash_expression(columns: tuple[str, ...]) -> str:
    """SHA-256 de la concatenación: el predicado del diff pasa de N
    comparaciones NULL-safe a una sola de 32 bytes.

    El COALESCE es obligatorio porque `concat_ws` OMITE los NULL: sin él,
    ('a', NULL, 'b') y ('a', 'b', NULL) darían el mismo hash.
    """
    parts = ", ".join(f"COALESCE({c}::text, '{NULL_SENTINEL}')" for c in columns)
    return f"sha256(convert_to(concat_ws({HASH_SEPARATOR}, {parts}), 'UTF8'))"


def _staging_keyed_sql() -> str:
    """`occ` desempata las colisiones de la clave de negocio (10 de 50.000
    filas: mismo cliente, día, producto y fondo con dos movimientos).

    El ROW_NUMBER ordena por CONTENIDO y no por posición en el archivo, que
    cambia entre cortes y haría el resultado irreproducible.
    """
    key = ", ".join(BUSINESS_KEY)
    return f"""
        CREATE TEMP TABLE stg_keyed ON COMMIT DROP AS
        WITH deduped AS (
            SELECT *, ROW_NUMBER() OVER (
                          PARTITION BY {key}
                          ORDER BY amount, description NULLS LAST
                      )::int AS occ
            FROM movimientos_stg
        )
        SELECT {hash_expression(BUSINESS_KEY + ('occ',))} AS business_key,
               {hash_expression(ATTRIBUTES)}              AS row_hash,
               id_cliente, movement_date, product, movement_type, fund,
               ROUND(amount::numeric, 2) AS amount,
               description, commercial_name, quality_flags
        FROM deduped
    """


DIFF_SQL = """
CREATE TEMP TABLE diff ON COMMIT DROP AS
SELECT COALESCE(s.business_key, c.business_key) AS business_key,
       CASE
           WHEN c.business_key IS NULL                 THEN 'INSERT'
           WHEN s.business_key IS NULL                 THEN 'DELETE'
           WHEN s.row_hash IS DISTINCT FROM c.row_hash THEN 'UPDATE'
           ELSE 'UNCHANGED'
       END                                      AS action,
       c.sk                                     AS prev_sk,
       COALESCE(c.version_num, 0) + 1           AS next_version,
       -- En una baja el corte nuevo no aporta datos: la lápida hereda el
       -- último valor conocido.
       COALESCE(s.row_hash,        c.row_hash)        AS row_hash,
       COALESCE(s.id_cliente,      c.id_cliente)      AS id_cliente,
       COALESCE(s.movement_date,   c.movement_date)   AS movement_date,
       COALESCE(s.product,         c.product)         AS product,
       COALESCE(s.movement_type,   c.movement_type)   AS movement_type,
       COALESCE(s.fund,            c.fund)            AS fund,
       COALESCE(s.amount,          c.amount)          AS amount,
       COALESCE(s.description,     c.description)     AS description,
       COALESCE(s.commercial_name, c.commercial_name) AS commercial_name,
       COALESCE(s.quality_flags,   c.quality_flags)   AS quality_flags
FROM stg_keyed s
FULL OUTER JOIN (SELECT * FROM movimientos_hist WHERE is_current) c
  ON c.business_key = s.business_key
-- Un registro ya dado de baja que sigue ausente no genera nada: esta línea es
-- lo que hace el pipeline idempotente.
WHERE NOT (s.business_key IS NULL AND c.is_deleted)
"""

CLOSE_SQL = """
UPDATE movimientos_hist h
SET    valid_to = %(snapshot)s, is_current = FALSE
FROM   diff d
WHERE  h.sk = d.prev_sk AND d.action IN ('UPDATE', 'DELETE')
"""

INSERT_SQL = """
INSERT INTO movimientos_hist (
    business_key, row_hash, id_cliente, movement_date, product, movement_type,
    fund, amount, description, commercial_name, valid_from, is_current,
    is_deleted, operation, version_num, quality_flags, source_file)
SELECT business_key, row_hash, id_cliente, movement_date, product,
       movement_type, fund, amount, description, commercial_name,
       %(snapshot)s, TRUE, (action = 'DELETE'), action, next_version,
       quality_flags, %(source_file)s
FROM   diff
WHERE  action IN ('INSERT', 'UPDATE', 'DELETE')
"""


def merge(conn: psycopg.Connection, snapshot: date, source_file: str,
          max_delete_ratio: float) -> MergeStats:
    """Corre dentro de la transacción abierta por el orquestador: el corte
    entra completo o no entra."""
    with conn.cursor() as cur:
        cur.execute(_staging_keyed_sql())
        cur.execute("CREATE INDEX ON stg_keyed (business_key)")
        # Sin ANALYZE el planner asume ~1.000 filas en una temp table y elige
        # nested loop en vez de hash join: a escala, horas en vez de segundos.
        cur.execute("ANALYZE stg_keyed")

        cur.execute(DIFF_SQL)
        cur.execute("ANALYZE diff")

        cur.execute("SELECT action, count(*) FROM diff GROUP BY action")
        stats = MergeStats.from_counts(dict(cur.fetchall()))

        cur.execute("SELECT count(*) FROM movimientos_hist "
                    "WHERE is_current AND NOT is_deleted")
        active = cur.fetchone()[0]
        if active and stats.deletes / active > max_delete_ratio:
            raise DeleteRatioExceeded(
                f"el corte daría de baja {stats.deletes} de {active} registros "
                f"vigentes ({stats.deletes / active:.1%} > {max_delete_ratio:.0%})"
            )

        cur.execute(CLOSE_SQL, {"snapshot": snapshot})
        cur.execute(INSERT_SQL, {"snapshot": snapshot, "source_file": source_file})

    return stats
