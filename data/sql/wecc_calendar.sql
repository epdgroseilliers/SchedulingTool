SELECT FlowDate, IsPeak
FROM PhysiqueWest.dbo.WECC_PowerCalendar_Detailed
WHERE FlowDate >= :start_date
AND FlowDate <= :stop_date;