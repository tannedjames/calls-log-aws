-- How often the OCR read a labelled call exactly, by the row's confidence in
-- steps of 0.05: where the rate drops is where the 0.85 threshold belongs.
SELECT floor(row_conf * 20) / 20 AS conf_from,
       count(*) AS calls,
       count_if(ocr_start_ts = true_start_ts
                AND coalesce(ocr_duration_s, 0) = coalesce(true_duration_s, 0)) AS exact
FROM calls_log.labels
WHERE real_call
GROUP BY floor(row_conf * 20) / 20
ORDER BY conf_from
