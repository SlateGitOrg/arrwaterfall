-- Closing ARR, logos, gross churn and trailing NRR / GRR, all in constant currency.
-- NRR = ARR now of the customers that had ARR $ttm_months months ago / their ARR then.
-- GRR caps each of those customers at their starting ARR.
CREATE OR REPLACE TABLE monthly_metrics AS
WITH snap AS (
    SELECT
        month_idx,
        month_end,
        SUM(arr_usd_constant) AS closing_arr,
        COUNT(*) FILTER (WHERE arr_lc > 0) AS logos
    FROM customer_arr
    GROUP BY month_idx, month_end
),
ttm AS (
    SELECT
        c.month_idx,
        SUM(c.arr_usd_constant) AS arr_now,
        SUM(p.arr_usd_constant) AS arr_then,
        SUM(LEAST(c.arr_usd_constant, p.arr_usd_constant)) AS arr_kept
    FROM customer_arr c
    JOIN customer_arr p
      ON p.customer_id = c.customer_id
     AND p.month_idx = c.month_idx - $ttm_months
     AND p.arr_lc > 0
    GROUP BY c.month_idx
)
SELECT
    s.month_idx,
    s.month_end,
    s.closing_arr,
    s.logos,
    COALESCE(b.churn, 0) AS gross_churn_arr,
    t.arr_now / t.arr_then AS nrr_ttm,
    t.arr_kept / t.arr_then AS grr_ttm
FROM snap s
LEFT JOIN ttm t USING (month_idx)
LEFT JOIN bridge b ON b.month_idx = s.month_idx AND b.view = 'constant'
ORDER BY s.month_idx;
