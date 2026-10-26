-- Why calls were flagged this month, by field and reason code.
SELECT flag_field, flag_reason, count(*) AS calls
FROM calls_log.calls
WHERE work_month = '2026-10' AND flagged
GROUP BY flag_field, flag_reason
ORDER BY calls DESC
