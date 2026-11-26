-- Month calendar and per-month rates.  The input tables (customers, contract_lines, fx_rates)
-- are loaded by the pipeline before this file runs.
CREATE OR REPLACE TABLE months AS
SELECT ROW_NUMBER() OVER (ORDER BY month_end) AS month_idx, month_end
FROM (SELECT DISTINCT month_end FROM fx_rates);

CREATE OR REPLACE TABLE rates AS
SELECT m.month_idx, r.currency, r.spot_usd, r.plan_usd
FROM fx_rates r
JOIN months m USING (month_end);
