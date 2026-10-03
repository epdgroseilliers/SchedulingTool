-- How often each PSE code appears in a market path on our own West tags.
--
-- Read-only, like everything that touches MAG.dbo.OATI_*. Two jobs: it ranks
-- a market's several PSE codes by the one this desk actually uses, and it
-- widens the set of codes offered in the tag builder -- only 81 of the 169
-- codes our tags use are in BilateralMarketPseMapping, so the mapping alone
-- would refuse a code we use every week.
SELECT
    LTRIM(RTRIM(ms.PSEcode)) AS PSECode,
    COUNT(*) AS Uses
FROM MAG.dbo.OATI_TagMS AS ms
JOIN MAG.dbo.OATI_Tag AS t
    ON t.TagIndex = ms.TagIndex
WHERE t.StartTime >= :start_time
  AND t.TestTag = 0
  AND t.GCA NOT IN ('PJM','CPLE','ISNE','ERCO','NYIS','ONT')
  AND t.LCA NOT IN ('PJM','CPLE','ISNE','ERCO','NYIS','ONT')
  AND ms.PSEcode IS NOT NULL
  AND LTRIM(RTRIM(ms.PSEcode)) <> ''
GROUP BY LTRIM(RTRIM(ms.PSEcode))
ORDER BY Uses DESC, PSECode;
