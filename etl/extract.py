"""Descubrimiento de cortes y lectura del Parquet en streaming."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

import pandas as pd
import pyarrow.parquet as pq

from .config import BATCH_SIZE

log = logging.getLogger(__name__)

SNAPSHOT_PATTERN = re.compile(r"_dia_T(\d*)\.parquet$", re.IGNORECASE)


def discover(data_dir: Path, base_date: date) -> list[tuple[Path, date]]:
    """Ordena los cortes por el sufijo T/T1/T2, no alfabéticamente: aplicar
    T+1 antes que T dejaría intervalos de validez solapados."""
    found: list[tuple[int, Path]] = []
    for path in data_dir.glob("*.parquet"):
        match = SNAPSHOT_PATTERN.search(path.name)
        if match:
            found.append((int(match.group(1) or 0), path))
        else:
            log.warning("Se ignora %s: no sigue el patrón *_dia_T<n>.parquet", path.name)
    found.sort()
    return [(path, base_date + timedelta(days=offset)) for offset, path in found]


def checksum(path: Path, block_size: int = 1 << 20) -> str:
    """SHA-256 del archivo, leído por bloques.

    Distingue "se reprocesó el mismo archivo" de "el proveedor reenvió el día
    con datos corregidos". Sin esto, la idempotencia por fecha ocultaría el
    segundo caso.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def read_batches(path: Path) -> Iterator[pd.DataFrame]:
    """Lee por lotes: el pico de memoria es proporcional a BATCH_SIZE, no al
    tamaño del archivo como en pd.read_parquet."""
    parquet = pq.ParquetFile(path)
    log.info("%s: %d filas", path.name, parquet.metadata.num_rows)

    for batch in parquet.iter_batches(batch_size=BATCH_SIZE):
        df = batch.to_pandas()
        if "id" in df.columns and "id_cliente" not in df.columns:
            df = df.rename(columns={"id": "id_cliente"})
        yield df
