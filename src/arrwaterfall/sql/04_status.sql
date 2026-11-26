-- Reported ARR per customer-month.  While a lapse is inside the grace window the last active
-- ARR is carried with status in_grace; past the window ARR is 0 (lapsed).  Rates are attached
-- here: plan rates give constant currency, month-end spot rates give the spot view.
CREATE OR REPLACE TABLE customer_arr AS
WITH carried AS (
    SELECT
        *,
        LAST_VALUE(CASE WHEN active_lc > 0 THEN active_lc END IGNORE NULLS) OVER (
            PARTITION BY customer_id
            ORDER BY month_idx
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS last_active_lc
    FROM customer_month
),
graded AS (
    SELECT
        *,
        CASE
            WHEN active_lc > 0 THEN 'active'
            WHEN last_active_lc IS NOT NULL AND month_end - last_end <= $grace_days THEN 'in_grace'
            WHEN last_active_lc IS NOT NULL THEN 'lapsed'
            ELSE 'none'
        END AS status
    FROM carried
)
SELECT
    g.customer_id,
    g.currency,
    g.first_contract_date,
    g.month_idx,
    g.month_end,
    g.active_lc,
    g.status,
    CASE g.status WHEN 'active' THEN g.active_lc WHEN 'in_grace' THEN g.last_active_lc ELSE 0 END
        AS arr_lc,
    r.plan_usd,
    r.spot_usd,
    CASE g.status WHEN 'active' THEN g.active_lc WHEN 'in_grace' THEN g.last_active_lc ELSE 0 END
        * r.plan_usd AS arr_usd_constant,
    CASE g.status WHEN 'active' THEN g.active_lc WHEN 'in_grace' THEN g.last_active_lc ELSE 0 END
        * r.spot_usd AS arr_usd_spot
FROM graded g
JOIN rates r ON r.month_idx = g.month_idx AND r.currency = g.currency;
