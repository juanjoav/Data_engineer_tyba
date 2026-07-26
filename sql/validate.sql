-- Invariantes del modelo SCD2. Cualquier fallo aborta con exit code 1.
DO $$
DECLARE
    bad BIGINT;
BEGIN

    SELECT count(*) INTO bad FROM (
        SELECT business_key FROM movimientos_hist
        WHERE is_current GROUP BY business_key HAVING count(*) > 1) x;
    ASSERT bad = 0, format('INV-1: %s claves con más de una versión vigente', bad);

    SELECT count(*) INTO bad FROM (
        SELECT valid_to, LEAD(valid_from) OVER (
                   PARTITION BY business_key ORDER BY valid_from) AS next_from
        FROM movimientos_hist) x
    WHERE next_from IS NOT NULL AND next_from <> valid_to;
    ASSERT bad = 0, format('INV-2: %s discontinuidades temporales', bad);

    SELECT count(*) INTO bad FROM (
        SELECT version_num, ROW_NUMBER() OVER (
                   PARTITION BY business_key ORDER BY valid_from) AS expected
        FROM movimientos_hist) x
    WHERE version_num <> expected;
    ASSERT bad = 0, format('INV-3: %s versiones mal numeradas', bad);

    SELECT count(*) INTO bad FROM movimientos_hist
    WHERE (is_current AND valid_to <> DATE '9999-12-31')
       OR (NOT is_current AND valid_to = DATE '9999-12-31');
    ASSERT bad = 0, format('INV-4: %s filas con is_current/valid_to inconsistentes', bad);

    SELECT count(*) INTO bad FROM movimientos_hist
    WHERE amount < 0
       OR (movement_type = 'OUT' AND signed_amount > 0)
       OR (movement_type = 'IN'  AND signed_amount < 0);
    ASSERT bad = 0, format('INV-5: %s filas con signo incoherente', bad);

    SELECT count(*) INTO bad FROM (
        SELECT rows_read - rows_rejected
             - (n_insert + n_update + n_unchanged) AS gap
        FROM etl_run) x
    WHERE gap <> 0;
    ASSERT bad = 0, format('INV-6: %s corridas pierden registros', bad);

    RAISE NOTICE 'Todas las invariantes se cumplen.';
END $$;
