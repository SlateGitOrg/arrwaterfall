"""Registry of every name the SQL pipeline, the exports and the Power BI model share.

The SQL files produce tables whose columns are listed here, the export step writes
DimMovementType from `MOVEMENT_TYPES`, and `validate-pbi` checks the semantic model's
measures against `MEASURES`.  A measure that is not registered here cannot ship.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_GRACE_DAYS = 60
SENSITIVITY_GRACE_DAYS = (0, 30, 60, 90)
TTM_MONTHS = 12

VIEWS = ("constant", "spot")
STATUSES = ("active", "in_grace", "lapsed", "none")

# Order matters: it is the order of the bridge columns and of DimMovementType.sort_order.
MOVEMENT_TYPES = ("new", "expansion", "contraction", "churn", "reactivation", "fx")

# +1 adds to closing ARR, -1 subtracts.  Amounts are stored as positive magnitudes
# except fx, which carries its own sign.
MOVEMENT_SIGN = {
    "new": 1,
    "expansion": 1,
    "contraction": -1,
    "churn": -1,
    "reactivation": 1,
    "fx": 1,
}

BRIDGE_COLUMNS = ("opening", *MOVEMENT_TYPES, "closing")


@dataclass(frozen=True)
class Measure:
    """A named measure: where the reference implementation computes it."""

    name: str
    table: str  # table produced by the SQL pipeline
    column: str  # column in that table
    description: str


MEASURES: tuple[Measure, ...] = (
    Measure("Opening ARR", "bridge", "opening", "ARR at the previous month-end."),
    Measure("Closing ARR", "bridge", "closing", "ARR at the month-end, in-grace ARR included."),
    Measure("New ARR", "bridge", "new", "First-ever ARR of a customer."),
    Measure("Expansion ARR", "bridge", "expansion", "Increase in a continuing customer's ARR."),
    Measure("Contraction ARR", "bridge", "contraction", "Decrease in a continuing customer's ARR."),
    Measure("Churned ARR", "bridge", "churn", "ARR lost when the grace window ran out."),
    Measure("Reactivated ARR", "bridge", "reactivation", "ARR of a customer back after churn."),
    Measure("FX Impact", "bridge", "fx", "Opening ARR times the change in spot rate."),
    Measure("NRR TTM", "monthly_metrics", "nrr_ttm", "Trailing-12-month net revenue retention."),
    Measure("GRR TTM", "monthly_metrics", "grr_ttm", "Trailing-12-month gross revenue retention."),
    Measure(
        "Logo Churn", "bridge", "churned_logos", "Customers whose churn is booked in the month."
    ),
)

MEASURE_NAMES = tuple(m.name for m in MEASURES)

MOVEMENT_LABELS = {
    "new": "New",
    "expansion": "Expansion",
    "contraction": "Contraction",
    "churn": "Churn",
    "reactivation": "Reactivation",
    "fx": "FX",
}
