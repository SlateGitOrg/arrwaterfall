-- Rule 4: movements per customer-month, first in contract currency (so classification never
-- depends on the exchange rate), then priced in both currency views.
CREATE OR REPLACE TABLE customer_movement AS
WITH paired AS (
    SELECT
        a.customer_id,
        a.currency,
        a.first_contract_date,
        a.month_idx,
        a.month_end,
        p.month_end AS prev_month_end,
        p.arr_lc AS open_lc,
        a.arr_lc AS close_lc,
        a.plan_usd,
        a.spot_usd,
        p.spot_usd AS prev_spot_usd,
        p.plan_usd AS prev_plan_usd,
        EXISTS (
            SELECT 1 FROM churn_event e
            WHERE e.customer_id = a.customer_id AND e.churn_month_idx = a.month_idx
        ) AS churn_booked
    FROM customer_arr a
    JOIN customer_arr p ON p.customer_id = a.customer_id AND p.month_idx = a.month_idx - 1
)
SELECT
    *,
    CASE WHEN open_lc = 0 AND close_lc > 0 AND first_contract_date > prev_month_end
         THEN close_lc ELSE 0 END AS new_lc,
    CASE WHEN open_lc > 0 AND close_lc > open_lc AND NOT churn_booked
         THEN close_lc - open_lc ELSE 0 END AS expansion_lc,
    CASE WHEN open_lc > 0 AND close_lc > 0 AND close_lc < open_lc AND NOT churn_booked
         THEN open_lc - close_lc ELSE 0 END AS contraction_lc,
    CASE WHEN open_lc > 0 AND (churn_booked OR close_lc = 0) THEN open_lc ELSE 0 END AS churn_lc,
    CASE WHEN close_lc > 0
          AND ((open_lc = 0 AND first_contract_date <= prev_month_end)
               OR (open_lc > 0 AND churn_booked))
         THEN close_lc ELSE 0 END AS reactivation_lc
FROM paired;

-- One row per customer-month and currency view.  The FX line is opening ARR times the change in
-- spot rate, so the other movements are priced at the current rate and the view closes exactly.
CREATE OR REPLACE TABLE customer_bridge AS
SELECT
    customer_id, currency, month_idx, month_end,
    'constant' AS view,
    open_lc * prev_plan_usd AS opening,
    new_lc * plan_usd AS new,
    expansion_lc * plan_usd AS expansion,
    contraction_lc * plan_usd AS contraction,
    churn_lc * plan_usd AS churn,
    reactivation_lc * plan_usd AS reactivation,
    CAST(0 AS DOUBLE) AS fx,
    close_lc * plan_usd AS closing
FROM customer_movement
UNION ALL
SELECT
    customer_id, currency, month_idx, month_end,
    'spot' AS view,
    open_lc * prev_spot_usd,
    new_lc * spot_usd,
    expansion_lc * spot_usd,
    contraction_lc * spot_usd,
    churn_lc * spot_usd,
    reactivation_lc * spot_usd,
    open_lc * (spot_usd - prev_spot_usd),
    close_lc * spot_usd
FROM customer_movement;
