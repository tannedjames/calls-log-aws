-- Hours per person per week, newest week first.
SELECT person_key, week_start, days_worked, hours, avg_hours_per_day
FROM calls_log.weekly_attendance
ORDER BY week_start DESC, person_key
