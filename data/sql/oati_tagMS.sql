-- READ ONLY. MAG.dbo.OATI_* is the live tagging record; nothing in this
-- repository may write to it, and every statement in these four files is a
-- SELECT. See data/tags_history.py.
--
-- The market path: who sells to whom, in order.
--
-- EnergyProduct is blank on a wheel-through, which is information rather
-- than absence -- a three-party chain reads as two if it is dropped.
-- ContractNumberList is the legacy `text` type and must be cast: `text`
-- cannot be compared or grouped, and pyodbc handles it inconsistently.
SELECT
    ms.TagIndex,
    ms.TagMSIndex,
    LTRIM(RTRIM(COALESCE(ms.PSEcode, '')))       AS PSEcode,
    LTRIM(RTRIM(COALESCE(ms.EnergyProduct, ''))) AS EnergyProduct,
    LTRIM(RTRIM(COALESCE(CAST(ms.ContractNumberList AS nvarchar(max)), '')))
                                                 AS ContractNumberList
FROM MAG.dbo.OATI_TagMS AS ms
WHERE ms.TagIndex IN (
    SELECT t.TagIndex
    FROM MAG.dbo.OATI_Tag AS t
    -- >>> west tag filter (byte-identical in all four oati_*.sql) >>>
    WHERE t.StartTime >= :start_time
      AND t.StartTime <  :stop_time
      AND t.TestTag = 0
      AND t.GCA NOT IN ('PJM','CPLE','ISNE','ERCO','NYIS','ONT')
      AND t.LCA NOT IN ('PJM','CPLE','ISNE','ERCO','NYIS','ONT')
      AND (:all_actions = 1 OR t.LastAction IN
           ('IMPLEMENTED','ADJUSTED','CURTAILED','EXTENDED','RELOADED','CONFIRMED'))
    -- <<< west tag filter <<<
)
ORDER BY ms.TagIndex, ms.TagMSIndex;
