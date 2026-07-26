--  Modelo SCD Tipo 2 para movimientos diarios.

-- importa: se reconstruye desde el Parquet.
CREATE UNLOGGED TABLE movimientos_stg (
    id_cliente      TEXT             NOT NULL,
    movement_date   DATE             NOT NULL,
    product         TEXT             NOT NULL,
    movement_type   TEXT             NOT NULL,
    fund            TEXT             NOT NULL,
    amount          DOUBLE PRECISION NOT NULL,
    description     TEXT,
    commercial_name TEXT,
    quality_flags   TEXT[]           NOT NULL DEFAULT '{}'
);

-- Histórico versionado. Append-only: nunca se borra ni se sobrescribe.
CREATE TABLE movimientos_hist (
    sk              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_key    BYTEA         NOT NULL,   
    row_hash        BYTEA         NOT NULL,   

    id_cliente      TEXT          NOT NULL,
    movement_date   DATE          NOT NULL,
    product         TEXT          NOT NULL,
    movement_type   TEXT          NOT NULL CHECK (movement_type IN ('IN','OUT')),
    fund            TEXT          NOT NULL,
    amount          NUMERIC(18,2) NOT NULL CHECK (amount >= 0),
    description     TEXT,
    commercial_name TEXT,


    signed_amount   NUMERIC(18,2) GENERATED ALWAYS AS
                    (CASE WHEN movement_type = 'OUT' THEN -amount ELSE amount END) STORED,


    valid_from      DATE          NOT NULL,
    valid_to        DATE          NOT NULL DEFAULT DATE '9999-12-31',
    is_current      BOOLEAN       NOT NULL DEFAULT TRUE,
    is_deleted      BOOLEAN       NOT NULL DEFAULT FALSE, 
    operation       TEXT          NOT NULL CHECK (operation IN ('INSERT','UPDATE','DELETE')),
    version_num     INTEGER       NOT NULL,

    quality_flags   TEXT[]        NOT NULL DEFAULT '{}',
    source_file     TEXT          NOT NULL,
    ingested_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- Invariante clave: como máximo una versión vigente por movimiento.
-- Es un índice, no una convención: la base rechaza la escritura si el pipeline tiene un bug.
CREATE UNIQUE INDEX ux_hist_current ON movimientos_hist (business_key) WHERE is_current;
CREATE INDEX ix_hist_cliente ON movimientos_hist (id_cliente) WHERE is_current;

-- Registros rechazados, con el payload crudo para poder reprocesarlos.
CREATE TABLE quarantine (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file    TEXT        NOT NULL,
    reject_reasons TEXT[]      NOT NULL,
    raw_payload    JSONB       NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Bitácora de corridas.
CREATE TABLE etl_run (
    run_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_date   DATE      NOT NULL UNIQUE,   -- un corte por fecha
    source_file     TEXT      NOT NULL,
    source_checksum TEXT      NOT NULL,
    rows_read     BIGINT      NOT NULL,
    rows_valid    BIGINT      NOT NULL,
    rows_rejected BIGINT      NOT NULL,
    n_insert      BIGINT      NOT NULL,
    n_update      BIGINT      NOT NULL,
    n_delete      BIGINT      NOT NULL,
    n_unchanged   BIGINT      NOT NULL,
    finished_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Estado vigente del negocio.
CREATE VIEW movimientos_vigentes AS
SELECT sk, id_cliente, movement_date, product, movement_type, fund,
       amount, signed_amount, description, commercial_name,
       quality_flags, valid_from, version_num
FROM   movimientos_hist
WHERE  is_current AND NOT is_deleted;
