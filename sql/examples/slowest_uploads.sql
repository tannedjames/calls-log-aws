-- The month's slowest uploads from Ready to Done.
SELECT upload_id, work_date, image_count, attempts,
       date_diff('second', ready_at, finished_at) AS turnaround_s
FROM calls_log.uploads
WHERE work_month = '2026-10' AND finished_at IS NOT NULL
ORDER BY turnaround_s DESC
LIMIT 20
