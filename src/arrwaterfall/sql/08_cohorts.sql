-- Cohort NRR by first-contract quarter, constant currency.  A cohort's base is its ARR at the
-- month-end that closes its quarter (the first month-end of the data for older cohorts);
-- age_months counts month-ends after that base.
CREATE OR REPLACE TABLE cohort_nrr AS
WITH member AS (
    SELECT
        customer_id,
        CAST(year(first_contract_date) AS VARCHAR) || '-Q'
            || CAST(quarter(first_contract_date) AS VARCHAR) AS cohort,
        CAST(date_trunc('quarter', first_contract_date) + INTERVAL 3 MONTH - INTERVAL 1 DAY
             AS DATE) AS cohort_end
    FROM customers
),
based AS (
    SELECT
        *,
        (SELECT MIN(month_idx) FROM months m WHERE m.month_end >= member.cohort_end) AS base_idx
    FROM member
),
aged AS (
    SELECT
        b.cohort,
        b.base_idx,
        a.month_idx - b.base_idx AS age_months,
        SUM(a.arr_usd_constant) AS arr,
        COUNT(*) FILTER (WHERE a.month_idx = b.base_idx AND a.arr_lc > 0) AS customers
    FROM based b
    JOIN customer_arr a ON a.customer_id = b.customer_id AND a.month_idx >= b.base_idx
    WHERE b.base_idx IS NOT NULL
    GROUP BY b.cohort, b.base_idx, a.month_idx - b.base_idx
)
SELECT
    cohort,
    base_idx,
    age_months,
    MAX(customers) OVER (PARTITION BY cohort) AS customers,
    FIRST_VALUE(arr) OVER (PARTITION BY cohort ORDER BY age_months) AS base_arr,
    arr,
    arr / NULLIF(FIRST_VALUE(arr) OVER (PARTITION BY cohort ORDER BY age_months), 0) AS nrr
FROM aged
ORDER BY cohort, age_months;
