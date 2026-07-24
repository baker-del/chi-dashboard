"""
CHI Health Score — 0 to 100 composite account health score.

Components (100 pts total):
  Churn Risk        30 pts  — HubSpot manual risk rating
  Pendo Engagement  25 pts  — Days since last platform login
  CSM Activity      25 pts  — Days since last email or meeting
  Invoice Health    10 pts  — Overdue invoice status (only flags >30 days past due)
  Renewal Risk      10 pts  — Days to contract renewal

Color bands:
  🟢 Healthy   65–100
  🟡 At Risk   35–64
  🔴 Critical   0–34

Note on missing data: None (no data available) returns a neutral/partial score rather than 0.
  Pendo None  → 8 pts  (unknown, not confirmed inactive)
  Contact None → 8 pts  (unknown, not confirmed no-touch)
This prevents accounts not tracked in Pendo or with no HubSpot activity history from
being incorrectly flagged as Critical when the issue is data coverage, not account health.
"""


def _churn_risk_pts(risk_score: int) -> int:
    return {0: 15, 1: 30, 2: 24, 3: 15, 4: 5, 5: 0}.get(risk_score, 15)


def _pendo_pts(days: float | None) -> int:
    if days is None:
        return 8   # unknown — no Pendo data; neutral, not worst-case
    if days <= 30:
        return 25
    if days <= 60:
        return 15
    if days <= 90:
        return 8
    if days <= 180:
        return 3
    return 0


def _contact_pts(days: float | None) -> int:
    if days is None:
        return 8   # unknown — no HubSpot activity data; neutral, not worst-case
    if days <= 14:
        return 25
    if days <= 30:
        return 18
    if days <= 60:
        return 10
    if days <= 90:
        return 3
    return 0


def _invoice_pts(days_overdue: float | None) -> int:
    """Only penalise invoices that are >30 days past due."""
    if days_overdue is None or days_overdue <= 30:
        return 10
    if days_overdue <= 60:
        return 5
    if days_overdue <= 90:
        return 2
    return 0


def _renewal_pts(days_to_renewal: float | None) -> int:
    if days_to_renewal is None:
        return 5   # no deal — neutral
    if days_to_renewal < 0:
        return 0   # overdue
    if days_to_renewal <= 30:
        return 4
    if days_to_renewal <= 60:
        return 6
    if days_to_renewal <= 90:
        return 8
    return 10


def calculate_chi(account: dict) -> dict:
    """
    Return a dict with:
      chi_score  — int 0–100
      chi_color  — 'green' | 'yellow' | 'red'
      chi_label  — 'Healthy' | 'At Risk' | 'Critical'
      chi_breakdown — dict of component scores (for tooltip/debug)
    """
    r = _churn_risk_pts(account.get("risk_score", 0))
    p = _pendo_pts(account.get("pendo_days_since_login"))
    c = _contact_pts(account.get("days_since_last_contact"))
    i = _invoice_pts(account.get("campfire_days_overdue"))
    n = _renewal_pts(account.get("days_to_renewal"))

    score = r + p + c + i + n

    if score >= 65:
        color, label = "green", "Healthy"
    elif score >= 35:
        color, label = "yellow", "At Risk"
    else:
        color, label = "red", "Critical"

    return {
        "chi_score": score,
        "chi_color": color,
        "chi_label": label,
        "chi_breakdown": {
            "churn_risk": r,
            "pendo": p,
            "contact": c,
            "invoice": i,
            "renewal": n,
        },
    }
