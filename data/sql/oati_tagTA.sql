-- READ ONLY. MAG.dbo.OATI_* is the live tagging record; nothing in this
-- repository may write to it, and every statement in these four files is a
-- SELECT. See data/tags_history.py.
--
-- Transmission allocations: the OASIS reservation behind a segment, and the
-- product it was bought under (7-F, 2-NH, 6-NN, 1-NS...). ContractNumber is
-- what the desk's spreadsheet calls "# trans".
--
-- ParentSegmentRef joins to OATI_TagPS.TagPSIndex. ParentSegmentIndex is a
-- different int on the same row and is NOT that key -- using it produces a
-- result set rather than an error, which is what makes it dangerous.
SELECT
    ta.TagIndex,
    ta.TagTAIndex,
    ta.ParentSegmentIndex,
    ta.ParentSegmentRef,
    LTRIM(RTRIM(COALESCE(ta.TP, '')))               AS TP,
    LTRIM(RTRIM(COALESCE(ta.TransProduct, '')))     AS TransProduct,
    LTRIM(RTRIM(COALESCE(ta.ContractNumber, '')))   AS ContractNumber,
    LTRIM(RTRIM(COALESCE(ta.TransCustCode, '')))    AS TransCustCode,
    LTRIM(RTRIM(COALESCE(ta.NITSResourceName, ''))) AS NITSResourceName
FROM MAG.dbo.OATI_TagTA AS ta
WHERE ta.TagIndex IN (
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
ORDER BY ta.TagIndex, ta.ParentSegmentRef, ta.TagTAIndex;
