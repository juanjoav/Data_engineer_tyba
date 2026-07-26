"""Catálogos de referencia y reglas de calidad.

REJECT = inutilizable, va a cuarentena con su payload crudo.
WARN   = usable pero sospechoso, entra al histórico marcado.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Literal

import pandas as pd

Severity = Literal["REJECT", "WARN"]

TYPE_MAP: dict[str, str] = {
    "in": "IN", "entrada": "IN",
    "out": "OUT", "salida": "OUT",
}

FUNDS: tuple[str, ...] = (
    "Balanceado", "Conservador", "Crecimiento", "Internacional",
    "Mercado Monetario", "Renta Fija", "Renta Variable",
)

PRODUCTS: tuple[str, ...] = (
    "Acciones", "Bonos", "CDT", "Cuenta de Ahorro", "Divisas",
    "ETF", "Fondo de Inversión", "Fondo de Pensión",
)

SIN_ENTIDAD = "SIN_ENTIDAD"


def fold(value: str) -> str:
    """Normaliza para hacer match contra el catálogo, no para almacenar."""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().lower()


def canonize(series: pd.Series, catalog: tuple[str, ...]) -> pd.Series:
    """Mapea variantes sucias al valor canónico (23 variantes de `fund` para 7
    fondos reales).

    El mapeo se construye sobre los valores DISTINTOS, no sobre las filas: el
    costo es O(distinct), que es lo que permite escalar.
    """
    lookup = {fold(v): v for v in catalog}
    values = series.astype("string")
    mapping = {v: lookup.get(fold(v)) for v in values.dropna().unique()}
    fallback = values.str.replace(r"\s+", " ", regex=True).str.strip()
    return values.map(mapping).fillna(fallback)


def parse_dates(series: pd.Series) -> pd.Series:
    """Formatos explícitos en orden, nunca inferencia: con inferencia
    '05/10/2024' se lee como 5-oct o 5-may según qué traiga el lote."""
    values = series.astype("string").str.strip()
    parsed = pd.to_datetime(values, format="%Y-%m-%d", errors="coerce")
    return parsed.fillna(pd.to_datetime(values, format="%d/%m/%Y", errors="coerce"))


@dataclass(frozen=True)
class Rule:
    code: str
    severity: Severity
    rationale: str
    predicate: Callable[[pd.DataFrame], pd.Series]


def _blank(series: pd.Series) -> pd.Series:
    return series.isna() | (series.astype("string").str.strip() == "")


RULES: tuple[Rule, ...] = (
    Rule("ID_NULO", "REJECT",
         "id_cliente vacío: el movimiento no es trazable a ningún titular.",
         lambda d: _blank(d["id_cliente"])),

    Rule("FECHA_INVALIDA", "REJECT",
         "date no parseable con ningún formato conocido.",
         lambda d: d["movement_date"].isna()),

    Rule("MONTO_NULO", "REJECT",
         "amount nulo: imputar 0 falsearía los estados financieros.",
         lambda d: d["amount"].isna()),

    Rule("TIPO_DESCONOCIDO", "REJECT",
         "type no mapea a IN/OUT: sin dirección el signo es indefinido.",
         lambda d: d["movement_type"].isna()),

    Rule("SIGNO_INCONSISTENTE", "WARN",
         "Monto negativo en una entrada. El 100% de los negativos del archivo "
         "son type=IN: el signo está doblemente codificado. Se guarda la "
         "magnitud y el signo se deriva de `type`.",
         lambda d: d["amount"] < 0),

    Rule("MONTO_CERO", "WARN",
         "Monto 0: económicamente vacío, pero puede ser un ajuste legítimo.",
         lambda d: d["amount"] == 0),

    Rule("DESCRIPCION_NULA", "WARN",
         "description nula: informativa, no contable. Se conserva NULL.",
         lambda d: _blank(d["description"])),

    Rule("ENTIDAD_NULA", "WARN",
         "commercial_name nulo. Forma parte de la clave, así que se canoniza: "
         "en SQL NULL <> NULL y dos filas iguales darían claves distintas.",
         lambda d: _blank(d["commercial_name"])),

    Rule("FONDO_NO_CATALOGADO", "WARN",
         "fund no reconocido tras normalizar mayúsculas, acentos y espacios.",
         lambda d: ~d["fund"].isin(FUNDS)),

    Rule("PRODUCTO_NO_CATALOGADO", "WARN",
         "product no reconocido en el catálogo.",
         lambda d: ~d["product"].isin(PRODUCTS)),
)

REJECT_RULES = tuple(r for r in RULES if r.severity == "REJECT")
WARN_RULES = tuple(r for r in RULES if r.severity == "WARN")
