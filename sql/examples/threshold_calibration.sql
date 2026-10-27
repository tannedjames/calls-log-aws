-- How often the OCR read a labelled call exactly, by the row's confidence in
-- steps of 0.05: where the rate drops is where the 0.85 threshold belongs.
-- A call labelled twice counts once, by its latest label (as ocr_accuracy).
SELECT floor(row_conf * 20) / 20 AS conf_from,
       count(*) AS calls,
       count_if(ocr_start_ts = true_start_ts
                AND coalesce(ocr_duration_s, 0) = coalesce(true_duration_s, 0)) AS exact
FROM (
  SELECT *, row_number() OVER (PARTITION BY upload_id, call_seq ORDER BY labelled_at DESC) AS latest
  FROM calls_log.labels
) l
WHERE latest = 1 AND real_call
GROUP BY floor(row_conf * 20) / 20
ORDER BY conf_from
