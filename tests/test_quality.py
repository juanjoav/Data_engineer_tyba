"""Pruebas de la capa de calidad. No requieren base de datos."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from etl.quality import FUNDS, canonize, parse_dates
from etl.transform import SIN_ENTIDAD, normalize, validate


def make_raw(**overrides) -> pd.DataFrame:
    base = {
        "id_cliente": "CLI000001", "date": "2024-09-22", "product": "Bonos",
        "type": "entrada", "fund": "Renta Fija", "amount": 1000.0,
        "description": "Compra de activo", "commercial_name": "BBVA",
    }
    base.update(overrides)
    return pd.DataFrame([base])


def run(raw: pd.DataFrame):
    return validate(normalize(raw), raw)


@pytest.mark.parametrize("dirty", [
    "RENTA FIJA", "renta fija", "  Renta Fija  ", "Renta  Fija", "rEnTa FiJa",
])
def test_variantes_de_fondo_convergen_al_canonico(dirty: str) -> None:
    assert canonize(pd.Series([dirty]), FUNDS).iloc[0] == "Renta Fija"


def test_fondo_desconocido_se_conserva_limpio_no_se_pierde() -> None:
    assert canonize(pd.Series(["  Fondo   Nuevo "]), FUNDS).iloc[0] == "Fondo Nuevo"


@pytest.mark.parametrize("tipo,esperado", [
    ("entrada", "IN"), ("Entrada", "IN"), ("ENTRADA", "IN"), ("IN", "IN"),
    ("in", "IN"), ("salida", "OUT"), ("SALIDA", "OUT"), ("out", "OUT"), ("OUT", "OUT"),
])
def test_las_diez_variantes_de_type_mapean_al_dominio(tipo, esperado) -> None:
    assert run(make_raw(type=tipo)).valid.iloc[0]["movement_type"] == esperado


def test_fecha_dmy_no_se_invierte() -> None:
    """05/10/2024 es 5 de octubre, nunca 10 de mayo."""
    parsed = parse_dates(pd.Series(["13/10/2024", "2024-10-13", "05/10/2024"]))
    assert parsed.iloc[0].date() == date(2024, 10, 13)
    assert parsed.iloc[1].date() == date(2024, 10, 13)
    assert parsed.iloc[2].date() == date(2024, 10, 5)


@pytest.mark.parametrize("override,codigo", [
    ({"amount": None}, "MONTO_NULO"),
    ({"id_cliente": "   "}, "ID_NULO"),
    ({"date": "no-es-fecha"}, "FECHA_INVALIDA"),
    ({"type": "transferencia"}, "TIPO_DESCONOCIDO"),
])
def test_registros_inutilizables_van_a_cuarentena(override, codigo) -> None:
    result = run(make_raw(**override))
    assert result.valid.empty
    assert codigo in result.rejected.iloc[0]["reject_reasons"]


def test_cuarentena_preserva_el_payload_crudo() -> None:
    result = run(make_raw(amount=None, description="algo raro"))
    assert "algo raro" in result.rejected.iloc[0]["raw_payload"]


def test_negativo_en_entrada_se_marca_y_guarda_como_magnitud() -> None:
    row = run(make_raw(amount=-500.0, type="entrada")).valid.iloc[0]
    assert "SIGNO_INCONSISTENTE" in row["quality_flags"]
    assert row["amount"] == 500.0


def test_monto_cero_se_conserva_marcado() -> None:
    result = run(make_raw(amount=0.0))
    assert len(result.valid) == 1
    assert "MONTO_CERO" in result.valid.iloc[0]["quality_flags"]


def test_entidad_nula_se_canoniza_porque_es_parte_de_la_clave() -> None:
    row = run(make_raw(commercial_name=None)).valid.iloc[0]
    assert row["commercial_name"] == SIN_ENTIDAD
    assert "ENTIDAD_NULA" in row["quality_flags"]


def test_descripcion_nula_se_conserva_nula() -> None:
    row = run(make_raw(description=None)).valid.iloc[0]
    assert pd.isna(row["description"])
    assert "DESCRIPCION_NULA" in row["quality_flags"]


def test_registro_limpio_no_acumula_marcas() -> None:
    result = run(make_raw())
    assert result.rejected.empty
    assert result.valid.iloc[0]["quality_flags"] == []


def test_ninguna_fila_desaparece() -> None:
    raw = pd.concat([make_raw(), make_raw(amount=None),
                     make_raw(amount=-1.0), make_raw(type="???")], ignore_index=True)
    result = run(raw)
    assert len(result.valid) + len(result.rejected) == len(raw)
