-- Days in the lake per month, by where they came from: the runner
-- (published) or a stored Day Sheet (xlsx_backfill).
SELECT work_month, source, count(*) AS days
FROM calls_log.day_summary
GROUP BY work_month, source
ORDER BY work_month, source
