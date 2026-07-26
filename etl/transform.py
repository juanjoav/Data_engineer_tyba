"""Aplicación de la normalización y las reglas de calidad a un lote.

Todo vectorizado sobre columnas completas: a un millón de filas, `iterrows`
sería la diferencia entre minutos y segundos.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import pandas as pd
import math

from .quality import (
    FUNDS,
    PRODUCTS,
    REJECT_RULES,
    SIN_ENTIDAD,
    TYPE_MAP,
    WARN_RULES,
    canonize,
    fold,
    parse_dates,
)

STAGING_COLUMNS: tuple[str, ...] = (
    "id_cliente", "movement_date", "product", "movement_type",
    "fund", "amount", "description", "commercial_name", "quality_flags",
)


@dataclass
class BatchResult:
    valid: pd.DataFrame
    rejected: pd.DataFrame
    rule_hits: dict[str, int] = field(default_factory=dict)

def to_json_payloads(frame: pd.DataFrame) -> list[str]:
    records = frame.to_dict("records")
    for record in records:
        for key, value in record.items():
            if isinstance(value, float) and not math.isfinite(value):
                record[key] = None
    return [json.dumps(r, ensure_ascii=False, default=str, allow_nan=False)
            for r in records]

def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=raw.index)

    out["id_cliente"] = raw["id_cliente"].astype("string").str.strip().str.upper()
    out["movement_date"] = parse_dates(raw["date"])
    out["product"] = canonize(raw["product"], PRODUCTS)
    out["fund"] = canonize(raw["fund"], FUNDS)

    types = raw["type"].astype("string")
    out["movement_type"] = types.map(
        {v: TYPE_MAP.get(fold(v)) for v in types.dropna().unique()}
    )

    out["amount"] = pd.to_numeric(raw["amount"], errors="coerce")

    for column in ("description", "commercial_name"):
        cleaned = (raw[column].astype("string")
                   .str.replace(r"\s+", " ", regex=True).str.strip())
        out[column] = cleaned.replace("", pd.NA)

    return out


def validate(norm: pd.DataFrame, raw: pd.DataFrame) -> BatchResult:
    hits: dict[str, int] = {}
    reasons = pd.Series([[] for _ in norm.index], index=norm.index)
    rejected_mask = pd.Series(False, index=norm.index)

    for rule in REJECT_RULES:
        mask = rule.predicate(norm).fillna(False)
        if mask.any():
            hits[rule.code] = int(mask.sum())
            reasons[mask] = reasons[mask].map(lambda lst, c=rule.code: lst + [c])
            rejected_mask |= mask

    # Se persiste el payload CRUDO: si el registro se rechazó, la normalización pudo ser justamente lo que falló.
    rejected = pd.DataFrame({
        "reject_reasons": reasons[rejected_mask],
        # "raw_payload": [
        #     json.dumps(record, ensure_ascii=False, default=str)
        #     for record in raw[rejected_mask.values].to_dict("records")
        # ],
        "raw_payload": to_json_payloads(raw[rejected_mask.values]),
    })

    valid = norm[~rejected_mask].copy()
    flags = pd.Series([[] for _ in valid.index], index=valid.index)

    for rule in WARN_RULES:
        mask = rule.predicate(valid).fillna(False)
        if mask.any():
            hits[rule.code] = int(mask.sum())
            flags[mask] = flags[mask].map(lambda lst, c=rule.code: lst + [c])

    # El signo lo dicta `type`; `amount` guarda solo la magnitud
    valid["amount"] = valid["amount"].abs().round(2)
    valid["commercial_name"] = valid["commercial_name"].fillna(SIN_ENTIDAD)
    valid["movement_date"] = valid["movement_date"].dt.date
    valid["quality_flags"] = flags

    return BatchResult(valid=valid[list(STAGING_COLUMNS)],
                       rejected=rejected, rule_hits=hits)
