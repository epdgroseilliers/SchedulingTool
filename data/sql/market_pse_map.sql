-- Every market's tagging PSE code(s).
--
-- BilateralMarket.MarketName is the desk's internal name for a counterparty
-- or market -- the key the back office uses, and the same string a trade row
-- carries (see data/matching.py). BilateralMarketPseMapping gives the PSE
-- code that name tags under. One market can have several: MSCG tags as both
-- RRWE01 and MSCG01, MAG as MAG001 and MMA.
--
-- Ordered by mapping Id so the caller has a stable fallback when it has no
-- usage history to rank them by.
SELECT
    m.MarketName,
    m.MarketFullName,
    p.PSECode,
    p.Id AS MappingId
FROM PhysiqueBilateral.dbo.BilateralMarket AS m
JOIN PhysiqueBilateral.dbo.BilateralMarketPseMapping AS p
    ON p.MarketId = m.Id
WHERE m.MarketName IS NOT NULL
  AND p.PSECode IS NOT NULL
ORDER BY m.MarketName, p.Id;
