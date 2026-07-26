"""Configuración del pipeline, resuelta desde variables de entorno."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
BASE_DATE = date.fromisoformat(os.getenv("BASE_DATE", "2024-10-15"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Fija limite de mem del proceso
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "200000"))

# Tope de cancelacion.
MAX_REJECT_RATIO = float(os.getenv("MAX_REJECT_RATIO", "0.10"))
MAX_DELETE_RATIO = float(os.getenv("MAX_DELETE_RATIO", "0.50"))

DSN = (
    f"host={os.getenv('PGHOST', 'postgres')} "
    f"port={os.getenv('PGPORT', '5432')} "
    f"dbname={os.getenv('PGDATABASE', 'movimientos')} "
    f"user={os.getenv('PGUSER', 'etl')} "
    f"password={os.getenv('PGPASSWORD', 'etl')} "
    f"application_name=etl_movimientos"
)


# Creacion de un ID a partir de campos
BUSINESS_KEY: tuple[str, ...] = (
    "id_cliente", "movement_date", "product",
    "movement_type", "fund", "commercial_name",
)
ATTRIBUTES: tuple[str, ...] = ("amount", "description")
