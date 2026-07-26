# Consolidación de movimientos diarios — SCD Tipo 2

```bash
docker compose up --build
```

Levanta PostgreSQL, crea el esquema, procesa `T` y `T+1` en orden e imprime el
resumen. Sin pasos previos.

## Resultado sobre los archivos entregados

| | `dia_T` | `dia_T1` |
|---|---|---|
| Leídas | 50.000 | 49.000 |
| Válidas | 48.457 | 47.560 |
| Cuarentena | 1.543 | 1.440 |
| Altas | 48.457 | 9.769 |
| Correcciones | 0 | 3.683 |
| Bajas | 0 | 10.666 |
| Sin cambios | 0 | 34.108 |

Cuadre: `34.108 + 3.683 + 10.666 = 48.457` (todas las claves de T quedan
explicadas) y `34.108 + 3.683 + 9.769 = 47.560` (todas las filas válidas de
T+1).

## El problema principal: no existe el `id` del glosario

El enunciado describe `id` como identificador de la transacción. El archivo
trae `id_cliente`, con **3.000 valores distintos sobre 50.000 filas** (16,7
movimientos por cliente): identifica al titular, no al movimiento. Tampoco hay
correspondencia posicional entre cortes.

Se construye entonces una clave de negocio explícita:

```
business_key = SHA256(id_cliente ⋮ date ⋮ product ⋮ type ⋮ fund ⋮ commercial_name ⋮ occ)
row_hash     = SHA256(amount ⋮ description)
```

`amount` y `description` quedan fuera de la clave porque son los campos que el
enunciado declara mutables: van al `row_hash`, que es lo que detecta las
correcciones.

**Limitaciones asumidas y medidas:** una corrección sobre un campo *de la
clave* se registra como baja + alta (inevitable sin identificador natural); y
la clave colisiona en **10 de 50.000 filas (0,02%)**, resueltas con un
`ROW_NUMBER` que ordena por contenido y no por posición en el archivo.

## Cómo escala

| Decisión | Alternativa | Razón |
|---|---|---|
| `iter_batches(200k)` | `pd.read_parquet()` | Pico de memoria proporcional al lote, no al archivo. |
| Diff en SQL (`FULL OUTER JOIN`) | `merge(how="outer")` en pandas | En pandas el pico es \|T\| + \|T+1\|: con 10M filas, 6-8 GB. |
| `COPY ... FORMAT BINARY` | `to_sql` / `executemany` | 300k-1M filas/s frente a 5-50k. |
| Staging `UNLOGGED` | Tabla normal | Sin WAL, 2-3x más rápido; se reconstruye desde el Parquet. |
| `ANALYZE` en las temp tables | Confiar en el planner | Sin estadísticas asume ~1.000 filas y elige nested loop: a escala, horas en vez de segundos. |

## Calidad de datos

Todo medido sobre los archivos entregados.

| Problema | Evidencia | Acción |
|---|---|---|
| `amount` nulo | 1.543 (3,1%) | **Rechazo**: imputar 0 falsearía los saldos. |
| `type` con 10 variantes | `entrada`/`ENTRADA`/`IN`/`out`… | Mapeo a `IN`/`OUT`; si no mapea, rechazo. |
| `fund` con 23 variantes | `RENTA FIJA`, `␣␣Renta Fija`, `Mercado␣␣Monetario` | Fold de acentos y espacios contra catálogo. |
| Fechas en 2 formatos | ISO 93% / `dd/mm/yyyy` 7% | Formatos explícitos en orden, nunca inferencia. |
| Montos negativos | 1.034 — **el 100% son `type=IN`** | El signo está doblemente codificado: se guarda la magnitud y el signo se deriva. |
| `amount = 0` | 940 | Se conserva marcado: puede ser un ajuste legítimo. |
| `description` nula | 4.563 (9,1%) | Se conserva NULL; nunca se inventa un valor. |
| `commercial_name` nulo | 8.307 (16,6%) | Forma parte de la clave → se canoniza (en SQL `NULL <> NULL`). |

Dos severidades: `REJECT` va a `quarantine` con el payload crudo en JSONB;
`WARN` entra al histórico marcado en `quality_flags`. Rechazar por todo
destruye información, aceptar todo destruye la confianza.

## Modelo

```sql
valid_from  DATE NOT NULL,                   -- intervalo semiabierto
valid_to    DATE NOT NULL DEFAULT '9999-12-31',
is_current  BOOLEAN NOT NULL,
is_deleted  BOOLEAN NOT NULL,                -- Nunca se hace un DELETE físico
operation   TEXT CHECK (operation IN ('INSERT','UPDATE','DELETE')),
version_num INTEGER NOT NULL

CREATE UNIQUE INDEX ux_hist_current ON movimientos_hist (business_key) WHERE is_current;
```


## Estructura

Cada módulo tiene una sola razón para cambiar.

```
├── docker-compose.yml    # Postgres + ETL, init automático, healthcheck
├── Dockerfile
├── sql/
│   ├── init.sql          # DDL (init automático de Postgres)
│   └── validate.sql      # invariantes del modelo
├── data/                 # Parquet de origen (montado :ro)
├── etl/
│   ├── __main__.py       # punto de entrada: python -m etl
│   ├── config.py         # configuración por entorno y clave de negocio
│   ├── extract.py        # descubre cortes y lee el Parquet por lotes
│   ├── quality.py        # catálogos y reglas (declarativo, lo revisa negocio)
│   ├── transform.py      # aplica normalización y reglas al lote
│   ├── database.py       # conexión y carga masiva con COPY
│   ├── scd2.py           # MERGE SCD2 en SQL
│   ├── pipeline.py       # orquestación, idempotencia y umbrales de aborto
│   └── report.py         # invariantes y resumen de cierre
└── tests/
    └── test_quality.py   # 27 casos, sin base de datos
```

`quality.py` y `transform.py` van separados a propósito: el primero declara
*qué* es un dato correcto y lo revisa el negocio; el segundo es la mecánica de
aplicarlo y no lo toca nadie. Son dos razones distintas para cambiar.


## Control

Cuatro mecanismos que no cambian el resultado, pero definen qué pasa
cuando algo va mal. Los cuatro están verificados, no solo escritos.

| Situación | Comportamiento |
|---|---|
| Se reejecuta el mismo corte con el mismo archivo | No-op explícito (checksum SHA-256) |
| Se reenvía el mismo corte con otro archivo | Falla: revertir versiones SCD2 debe ser una decisión humana |
| Llega un corte anterior al último historificado | Falla: dejaría intervalos de validez solapados |
| El archivo llega truncado | Rollback si las bajas superan `MAX_DELETE_RATIO` (50%) |
| Cambia el esquema de origen | Rollback si los rechazos superan `MAX_REJECT_RATIO` (10%) |
| El histórico se corrompe | `sql/validate.sql` lo detecta y el proceso sale con código 1 |
| La cuarentena guarda un JSON mal serializado | Idem (INV-7) |

Un archivo truncado se ve *exactamente igual* que "hoy hubo muchísimas
cancelaciones". Ante la ambigüedad, no se carga nada.

Las seis invariantes que se verifican en cada corrida: una versión vigente por
movimiento, intervalos contiguos sin solapes, `version_num` correlativo,
coherencia `is_current`/`valid_to`, signo derivado del tipo, conservación
(`leídas = rechazadas + altas + correcciones + sin_cambio`) y payloads de
cuarentena serializados como objeto JSONB consultable.

## Consultas útiles

```sql
SELECT * FROM movimientos_vigentes;          -- estado de hoy
SELECT * FROM etl_run;                       -- qué pasó en cada corte
SELECT * FROM quarantine;                    -- rechazados, reprocesables

-- Estado a una fecha pasada
SELECT * FROM movimientos_hist
WHERE '2024-10-15' >= valid_from AND '2024-10-15' < valid_to AND NOT is_deleted;
```
