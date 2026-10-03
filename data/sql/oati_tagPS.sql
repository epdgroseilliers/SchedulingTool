-- READ ONLY. MAG.dbo.OATI_* is the live tagging record; nothing in this
-- repository may write to it, and every statement in these four files is a
-- SELECT. See data/tags_history.py.
--
-- The physical path: the actual wires. One 'G' row, one 'L' row and a 'T'
-- row per transmission segment, on every West tag without exception.
--
-- MSIndex says which market-path segment owns a physical one. Join on
-- (TagIndex, MSIndex), never MSIndex alone -- it is only unique inside one
-- tag. Same for TagPSIndex, which OATI_TagTA refers back to.
--
-- POR_Name and POD_Name are char(256): trimmed here rather than in Python
-- because untrimmed padding is most of what the query would transfer, and
-- because 'MATL  ' and 'MATL' would split one route into two recipes.
SELECT
    ps.TagIndex,
    ps.TagPSIndex,
    ps.MSIndex,
    LTRIM(RTRIM(COALESCE(ps.MSPSEcode, '')))  AS MSPSEcode,
    LTRIM(RTRIM(COALESCE(ps.Type, '')))       AS Type,
    LTRIM(RTRIM(COALESCE(ps.TPcode, '')))     AS TPcode,
    LTRIM(RTRIM(COALESCE(ps.CAcode, '')))     AS CAcode,
    LTRIM(RTRIM(COALESCE(ps.POR_Name, '')))   AS POR_Name,
    LTRIM(RTRIM(COALESCE(ps.POR_CAcode, ''))) AS POR_CAcode,
    LTRIM(RTRIM(COALESCE(ps.POD_Name, '')))   AS POD_Name,
    LTRIM(RTRIM(COALESCE(ps.POD_CAcode, ''))) AS POD_CAcode,
    LTRIM(RTRIM(COALESCE(ps.MOcode, '')))     AS MOcode
FROM MAG.dbo.OATI_TagPS AS ps
WHERE ps.TagIndex IN (
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
ORDER BY ps.TagIndex, ps.MSIndex, ps.TagPSIndex;
