-- One row per day a Run finished: uploads, images, re-tries, turnaround
-- (Ready -> Done) and Run time percentiles in seconds, and the Lambda
-- compute as estimated GB-s (Run seconds x memory / 1024; a Run resumed from
-- checkpoints counts its last start only). Backfilled uploads have no memory
-- size, so they add to the counts and times but not to the GB-s.
CREATE OR REPLACE VIEW calls_log.pipeline_metrics AS
SELECT
  CAST(finished_at AS date) AS finished_date,
  count(*) AS uploads,
  sum(image_count) AS images,
  count_if(attempts > 1) AS retried,
  approx_percentile(date_diff('second', ready_at, finished_at), 0.5) AS turnaround_p50_s,
  approx_percentile(date_diff('second', ready_at, finished_at), 0.95) AS turnaround_p95_s,
  approx_percentile(date_diff('second', started_at, finished_at), 0.5) AS run_p50_s,
  round(sum(date_diff('second', started_at, finished_at) * runner_memory_mb / 1024.0), 1) AS est_gb_s,
  round(sum(date_diff('second', started_at, finished_at) * runner_memory_mb / 1024.0)
        / nullif(sum(CASE WHEN runner_memory_mb IS NOT NULL THEN image_count END), 0), 2) AS est_gb_s_per_image
FROM calls_log.uploads
WHERE finished_at IS NOT NULL
GROUP BY CAST(finished_at AS date)
