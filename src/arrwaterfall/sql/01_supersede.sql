-- Rule 1: a replaced line stops counting the day before its replacement starts.
-- A renewal booked with a one-day overlap therefore never counts twice at a month-end.
CREATE OR REPLACE TABLE line_effective AS
WITH first_replacement AS (
    SELECT replaces_line_id AS line_id, MIN(start_date) AS replaced_from
    FROM contract_lines
    WHERE replaces_line_id IS NOT NULL
    GROUP BY replaces_line_id
)
SELECT
    l.line_id,
    l.customer_id,
    l.product,
    l.start_date,
    l.end_date,
    l.annualised_amount AS amount_lc,
    l.replaces_line_id,
    LEAST(l.end_date, COALESCE(f.replaced_from - 1, l.end_date)) AS eff_end
FROM contract_lines l
LEFT JOIN first_replacement f USING (line_id);
