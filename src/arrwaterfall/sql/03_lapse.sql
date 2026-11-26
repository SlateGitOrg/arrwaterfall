-- Rule 3a, gaps and islands at day level.  Contract coverage of a customer is the union of its
-- [start, effective end] intervals.  A new island starts when a line begins more than one day
-- after everything before it ended; gap_days counts the uncovered days in between.
CREATE OR REPLACE TABLE coverage_gap AS
WITH live AS (
    SELECT customer_id, line_id, start_date, eff_end
    FROM line_effective
    WHERE eff_end >= start_date
),
ordered AS (
    SELECT
        customer_id,
        start_date,
        MAX(eff_end) OVER (
            PARTITION BY customer_id
            ORDER BY start_date, line_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS prev_end
    FROM live
),
returned AS (
    SELECT customer_id, prev_end AS lapse_after, start_date AS return_date
    FROM ordered
    WHERE prev_end IS NOT NULL AND start_date > prev_end + 1
),
open_ended AS (
    SELECT customer_id, MAX(eff_end) AS lapse_after, CAST(NULL AS DATE) AS return_date
    FROM live
    GROUP BY customer_id
    HAVING MAX(eff_end) < (SELECT MAX(month_end) FROM months)
),
gaps AS (
    SELECT * FROM returned
    UNION ALL
    SELECT * FROM open_ended
)
SELECT
    g.customer_id,
    g.lapse_after,
    g.return_date,
    g.return_date - g.lapse_after - 1 AS gap_days,
    -- a gap only exists in the monthly series if it contains a month-end
    EXISTS (
        SELECT 1 FROM months m
        WHERE m.month_end > g.lapse_after
          AND (g.return_date IS NULL OR m.month_end < g.return_date)
    ) AS spans_month_end
FROM gaps g;

-- Rule 3b: a gap longer than the grace window is churn, booked in the month that contains
-- lapse + grace + 1 day; the return is a reactivation in the month that contains the new start.
-- The two months can be the same month.
CREATE OR REPLACE TABLE churn_event AS
SELECT
    customer_id,
    lapse_after,
    return_date,
    gap_days,
    (SELECT MIN(month_idx) FROM months m WHERE m.month_end >= g.lapse_after + $grace_days + 1)
        AS churn_month_idx,
    (SELECT MIN(month_idx) FROM months m WHERE m.month_end >= g.return_date)
        AS reactivation_month_idx
FROM coverage_gap g
WHERE spans_month_end
  AND (return_date IS NULL OR gap_days > $grace_days)
  AND EXISTS (SELECT 1 FROM months m WHERE m.month_end >= g.lapse_after + $grace_days + 1);
