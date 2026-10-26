-- One row per person and week (weeks start on Monday): days with sessions,
-- hours, and the average day. Reads day_summary, so create that first.
CREATE OR REPLACE VIEW calls_log.weekly_attendance AS
SELECT
  person_key,
  date_trunc('week', work_date) AS week_start,
  count(*) AS days_worked,
  sum(span_min) AS span_min,
  round(sum(span_min) / 60.0, 2) AS hours,
  round(avg(span_min) / 60.0, 2) AS avg_hours_per_day
FROM calls_log.day_summary
GROUP BY person_key, date_trunc('week', work_date)
