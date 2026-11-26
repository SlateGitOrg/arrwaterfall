-- Rule 5: monthly bridge per currency view, with the largest customer-level residual so a leak
-- anywhere shows up in the summary.
CREATE OR REPLACE TABLE bridge AS
SELECT
    view,
    month_idx,
    month_end,
    SUM(opening) AS opening,
    SUM(new) AS new,
    SUM(expansion) AS expansion,
    SUM(contraction) AS contraction,
    SUM(churn) AS churn,
    SUM(reactivation) AS reactivation,
    SUM(fx) AS fx,
    SUM(closing) AS closing,
    MAX(ABS(opening + new + expansion - contraction - churn + reactivation + fx - closing))
        AS max_residual,
    COUNT(*) FILTER (WHERE churn > 0) AS churned_logos,
    COUNT(*) FILTER (WHERE new > 0) AS new_logos,
    COUNT(*) FILTER (WHERE reactivation > 0) AS reactivated_logos
FROM customer_bridge
GROUP BY view, month_idx, month_end;
