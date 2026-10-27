-- Published days whose sessions don't account for every countable call.
-- The publisher refuses such days, so this should return no rows.
SELECT s.person_key, s.work_date,
       sum(s.call_count) AS calls_in_sessions,
       max(d.countable_calls) AS countable_calls
FROM calls_log.sessions s
JOIN calls_log.day_summary d
  ON d.work_month = s.work_month AND d.person_key = s.person_key AND d.work_date = s.work_date
WHERE s.work_month = '2026-10' AND s.source = 'runner'
GROUP BY s.person_key, s.work_date
HAVING sum(s.call_count) <> max(d.countable_calls)
