-- Rule 2: customer ARR at each month-end in contract currency.  A line is active at month-end d
-- when start <= d <= effective end.  last_end is the latest effective end among lines that have
-- started by d; when nothing is active it is the end of the previous contract island.
CREATE OR REPLACE TABLE customer_month AS
SELECT
    c.customer_id,
    c.currency,
    c.first_contract_date,
    m.month_idx,
    m.month_end,
    COALESCE(SUM(l.amount_lc) FILTER (WHERE m.month_end <= l.eff_end), 0) AS active_lc,
    MAX(l.eff_end) AS last_end
FROM customers c
CROSS JOIN months m
LEFT JOIN line_effective l
    ON l.customer_id = c.customer_id
   AND l.start_date <= m.month_end
   AND l.eff_end >= l.start_date
GROUP BY c.customer_id, c.currency, c.first_contract_date, m.month_idx, m.month_end;
