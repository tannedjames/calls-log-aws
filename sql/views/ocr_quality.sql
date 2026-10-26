-- The label-free half of OCR accuracy (Phase 4.5): per day, person, model
-- and parser version, how many calls were flagged or read with a confidence
-- under the 0.85 threshold, per field. Exact-match accuracy needs labelled
-- calls and comes later.
CREATE OR REPLACE VIEW calls_log.ocr_quality AS
SELECT
  work_month,
  work_date,
  person_key,
  ocr_model,
  parser_version,
  count(*) AS calls,
  count_if(flagged) AS flagged,
  count_if(row_conf < 0.85) AS low_row_conf,
  count_if(start_conf < 0.85) AS low_start_conf,
  count_if(duration_conf < 0.85) AS low_duration_conf,
  round(avg(row_conf), 4) AS mean_row_conf,
  round(min(start_conf), 4) AS min_start_conf,
  round(min(duration_conf), 4) AS min_duration_conf
FROM calls_log.calls
GROUP BY work_month, work_date, person_key, ocr_model, parser_version
