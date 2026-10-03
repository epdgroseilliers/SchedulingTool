-- READ ONLY. MAG.dbo.OATI_* is the live tagging record; nothing in this
-- repository may write to it, and every statement in these four files is a
-- SELECT. See data/tags_history.py.
--
-- One West tag per row: the header the other three files hang off.
--
-- "West" is the desk's own definition -- neither control area is an Eastern,
-- Texan or Ontario market. The LastAction list keeps tags that actually
-- flowed and drops WITHDRAWN, CANCELLED, DENIED, EXPIRED and TERMINATED: a
-- route the desk pulled back is not a route to learn from.
--
-- Times are UTC. One row per TagID -- OATI keeps no amendment history here,
-- confirmed by counting (11,033 rows, 11,033 distinct TagIDs in 2026).
SELECT
    t.TagIndex,
    t.TagID,
    LTRIM(RTRIM(COALESCE(t.TagCode, '')))    AS TagCode,
    LTRIM(RTRIM(COALESCE(t.GCA, '')))        AS GCA,
    LTRIM(RTRIM(COALESCE(t.LCA, '')))        AS LCA,
    LTRIM(RTRIM(COALESCE(t.CPSE, '')))       AS CPSE,
    t.StartTime, t.StopTime, t.CreationTime,
    LTRIM(RTRIM(COALESCE(t.PseComment, ''))) AS PseComment,
    LTRIM(RTRIM(COALESCE(t.LastAction, ''))) AS LastAction,
    t.TagCompositeState
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
ORDER BY t.StartTime, t.TagID;
