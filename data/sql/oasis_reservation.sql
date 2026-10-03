-- READ ONLY. One OASIS transmission reservation, by its assignment
-- reference -- the number a scheduler writes in the tag sheet's "# trans"
-- column. See data/reservations.py.
--
-- OATI_TransmissionSummary_Hourly holds one row per reservation *per hour*,
-- so this collapses them: PathName, the provider and the two points are
-- constant across a reservation's rows (checked -- every AssignmentRef in
-- the table has exactly one of each), while the capacity and the window are
-- aggregated over them. Status is CONFIRMED on every row of this table; it
-- is carried anyway rather than assumed.
--
-- AssignmentRef is nvarchar, so the bind is compared without conversion.
SELECT
    LTRIM(RTRIM(s.AssignmentRef))              AS AssignmentRef,
    LTRIM(RTRIM(MAX(s.PathName)))              AS PathName,
    LTRIM(RTRIM(MAX(s.PrimaryProviderCode)))   AS Provider,
    LTRIM(RTRIM(MAX(s.PointOfReceipt)))        AS POR,
    LTRIM(RTRIM(MAX(s.PointOfDelivery)))       AS POD,
    LTRIM(RTRIM(MAX(s.Status)))                AS Status,
    LTRIM(RTRIM(MAX(s.TsClass)))               AS TsClass,
    MAX(s.CapacityGranted)                     AS MaxGranted,
    MIN(s.DATE)                                AS FirstDate,
    MAX(s.DATE)                                AS LastDate
FROM MAG.dbo.OATI_TransmissionSummary_Hourly AS s
WHERE s.AssignmentRef = :aref
GROUP BY LTRIM(RTRIM(s.AssignmentRef));
