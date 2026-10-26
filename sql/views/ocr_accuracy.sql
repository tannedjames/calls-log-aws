-- The labelled half of OCR accuracy (Phase 4.5): per model, parser version
-- and confidence band (the row's confidence against the 0.85 threshold), how
-- many hand-labelled calls the OCR read exactly. The start is compared to the
-- minute (a call log shows no seconds), the duration to the second, a missed
-- call's none counting as 0. A call labelled twice counts once, by its latest
-- label. Labels start from calls the OCR read, so calls it missed altogether
-- aren't measured here.
CREATE OR REPLACE VIEW calls_log.ocr_accuracy AS
SELECT
  ocr_model,
  parser_version,
  CASE WHEN row_conf IS NULL THEN 'unknown' WHEN row_conf < 0.85 THEN 'low' ELSE 'high' END AS conf_band,
  count(*) AS labelled,
  count_if(NOT real_call) AS not_a_call,
  count_if(real_call) AS calls,
  count_if(real_call AND start_ok) AS start_exact,
  count_if(real_call AND duration_ok) AS duration_exact,
  count_if(real_call AND start_ok AND duration_ok) AS both_exact,
  round(CAST(count_if(real_call AND start_ok) AS double) / nullif(count_if(real_call), 0), 4) AS start_exact_rate,
  round(CAST(count_if(real_call AND duration_ok) AS double) / nullif(count_if(real_call), 0), 4) AS duration_exact_rate,
  round(avg(CASE WHEN real_call THEN abs(coalesce(ocr_duration_s, 0) - coalesce(true_duration_s, 0)) END), 1)
    AS duration_mean_error_s
FROM (
  SELECT *,
         ocr_start_ts = true_start_ts AS start_ok,
         coalesce(ocr_duration_s, 0) = coalesce(true_duration_s, 0) AS duration_ok,
         row_number() OVER (PARTITION BY upload_id, call_seq ORDER BY labelled_at DESC) AS latest
  FROM calls_log.labels
) l
WHERE latest = 1
GROUP BY ocr_model, parser_version, CASE WHEN row_conf IS NULL THEN 'unknown' WHEN row_conf < 0.85 THEN 'low' ELSE 'high' END
