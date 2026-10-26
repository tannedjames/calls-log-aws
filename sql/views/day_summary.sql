-- One row per person and day: the Day Sheet's sessions in hours, and the
-- day's calls where the runner published them (null for xlsx_backfill days).
-- Filter on work_month ('yyyy-MM') so a query reads one month's files only.
CREATE OR REPLACE VIEW calls_log.day_summary AS
SELECT
  s.work_month,
  s.person_key,
  s.work_date,
  max(s.source) AS source,
  count(*) AS sessions,
  sum(s.span_min) AS span_min,
  round(sum(s.span_min) / 60.0, 2) AS hours,
  min(s.start_ts) AS first_start,
  max(s.end_ts) AS last_end,
  max(c.calls) AS calls,
  max(c.countable_calls) AS countable_calls,
  max(c.flagged_calls) AS flagged_calls
FROM calls_log.sessions s
LEFT JOIN (
  SELECT work_month, person_key, work_date,
         count(*) AS calls,
         count_if(countable) AS countable_calls,
         count_if(flagged) AS flagged_calls
  FROM calls_log.calls
  GROUP BY work_month, person_key, work_date
) c
  ON c.work_month = s.work_month AND c.person_key = s.person_key AND c.work_date = s.work_date
GROUP BY s.work_month, s.person_key, s.work_date
