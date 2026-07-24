import os
import re
import time
import requests
import yaml
from collections import defaultdict
from datetime import date, timedelta

CAMPFIRE_BASE = "https://api.meetcampfire.com"
MAPPINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "config", "campfire_mappings.yaml")


def _campfire_headers():
    key = os.getenv("CAMPFIRE_API_KEY")
    if not key:
        raise RuntimeError("CAMPFIRE_API_KEY not set in environment")
    return {"Authorization": f"Token {key}", "Content-Type": "application/json"}


def _normalize(name: str) -> str:
    if not name:
        return ""
    name = name.lower()
    name = re.sub(
        r'\b(llc|inc|corp|co|ltd|dba|the|group|staffing|solutions|services|talent|'
        r'consulting|associates|lp|llp|international|main|corporate|enterprises|'
        r'personnel|professionals|workforce|partners)\b',
        '', name
    )
    name = re.sub(r'[^a-z0-9 ]', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def _annualize(contract: dict) -> float:
    """Derive ARR from total_contract_value + contract duration."""
    value = float(contract.get("total_contract_value") or 0)
    if value <= 0:
        return 0.0
    start = contract.get("contract_start_date")
    end = contract.get("contract_end_date")
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
        months = max((e - s).days / 30.44, 1)
        return round(value / months * 12, 2)
    except Exception:
        return round(value, 2)


def load_mappings() -> dict:
    try:
        with open(MAPPINGS_PATH, "r") as f:
            data = yaml.safe_load(f) or {}
        return {
            "mappings": data.get("mappings") or {},
            "disregarded_hubspot": data.get("disregarded_hubspot") or [],
            "disregarded_campfire": data.get("disregarded_campfire") or [],
        }
    except FileNotFoundError:
        return {"mappings": {}, "disregarded_hubspot": [], "disregarded_campfire": []}


def save_mappings(mappings: dict, disregarded_hubspot: list, disregarded_campfire: list) -> None:
    data = {
        "mappings": mappings or {},
        "disregarded_hubspot": disregarded_hubspot or [],
        "disregarded_campfire": disregarded_campfire or [],
    }
    with open(MAPPINGS_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _fetch_all_contracts() -> list:
    contracts = []
    url = f"{CAMPFIRE_BASE}/rr/api/v1/contracts"
    params = {"limit": 100}
    while url:
        r = _campfire_get(url, params)
        data = r.json()
        contracts.extend(data.get("results", []))
        url = data.get("next")
        params = {}
    return contracts


def _campfire_get(url, params=None) -> requests.Response:
    """GET with retry on 429, 5xx, and transient network errors."""
    headers = _campfire_headers()
    for attempt in range(5):
        try:
            r = requests.get(url, headers=headers, params=params or {}, timeout=60)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ReadTimeout):
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise
        if r.ok:
            return r
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", 10)))
        elif r.status_code in (502, 503, 504) and attempt < 4:
            time.sleep(2 ** attempt)
        else:
            r.raise_for_status()
    r.raise_for_status()
    return r


def _fetch_all_invoices() -> list:
    """Fetch all invoices from Campfire with retry on transient errors."""
    invoices = []
    url = f"{CAMPFIRE_BASE}/coa/api/v1/invoice/"
    params = {"limit": 100, "sort": "-due_date"}
    while url:
        r = _campfire_get(url, params)
        data = r.json()
        invoices.extend(data.get("results", []))
        url = data.get("next")
        params = {}
    return invoices


def _build_invoice_index(invoices: list) -> dict:
    """
    Aggregate invoice data per contract ID.
    Returns dict: contract_id (int) -> {
        open_count       — all unpaid invoices
        open_amount      — total amount due across open invoices
        days_open        — days since oldest open invoice's invoice_date
        overdue_count    — open invoices where due_date < today
        overdue_amount   — total overdue amount
        days_overdue     — days past due on oldest overdue invoice
    }
    """
    today = date.today()
    index: dict[int, dict] = defaultdict(lambda: {
        "open_count": 0,
        "open_amount": 0.0,
        "days_open": 0,
        "overdue_count": 0,
        "overdue_amount": 0.0,
        "days_overdue": 0,
    })

    for inv in invoices:
        contract_id = inv.get("contract")
        if not contract_id:
            continue
        amount_due = float(inv.get("amount_due") or 0)
        if amount_due <= 0:
            continue
        if inv.get("status") != "open":
            continue

        index[contract_id]["open_count"] += 1
        index[contract_id]["open_amount"] += amount_due

        # Days open — from invoice_date (when issued)
        inv_date_str = inv.get("invoice_date")
        if inv_date_str:
            try:
                inv_date = date.fromisoformat(inv_date_str)
                days = (today - inv_date).days
                if days > index[contract_id]["days_open"]:
                    index[contract_id]["days_open"] = days
            except Exception:
                pass

        # Overdue — due_date passed by more than 30 days
        due_str = inv.get("due_date")
        if due_str:
            try:
                due = date.fromisoformat(due_str)
                days_past = (today - due).days
                if days_past > 30:
                    index[contract_id]["overdue_count"] += 1
                    index[contract_id]["overdue_amount"] += amount_due
                    if days_past > index[contract_id]["days_overdue"]:
                        index[contract_id]["days_overdue"] = days_past
            except Exception:
                pass

    for v in index.values():
        v["open_amount"] = round(v["open_amount"], 2)
        v["overdue_amount"] = round(v["overdue_amount"], 2)

    return index


def get_campfire_data() -> dict:
    """
    Fetch all contracts + invoices. Returns two lookup indices:
      by_deal_id — HubSpot deal_id (str) → account summary
      by_name    — normalized client name → aggregated account summary
    """
    contracts = _fetch_all_contracts()
    invoices  = _fetch_all_invoices()
    inv_index = _build_invoice_index(invoices)

    by_deal_id: dict[str, dict] = {}
    name_contracts: dict[str, list] = defaultdict(list)

    for raw in contracts:
        contract_id = raw.get("id")
        inv = inv_index.get(contract_id, {})

        summary = {
            "campfire_name":       raw.get("client_name", ""),
            "deal_id":             str(raw.get("deal_id") or ""),
            "status":              raw.get("status", ""),
            "arr":                 _annualize(raw),
            "total_contract_value": float(raw.get("total_contract_value") or 0),
            "billing_frequency":   raw.get("billing_frequency", ""),
            "contract_start_date": raw.get("contract_start_date"),
            "contract_end_date":   raw.get("contract_end_date"),
            "auto_renew":          raw.get("auto_renew", False),
            # Invoice data (real, from invoice endpoint)
            "open_invoice_count":  inv.get("open_count", 0),
            "open_amount":         inv.get("open_amount", 0.0),
            "days_open":           inv.get("days_open", 0),
            "overdue_invoice_count": inv.get("overdue_count", 0),
            "overdue_amount":      inv.get("overdue_amount", 0.0),
            "days_overdue":        inv.get("days_overdue", 0),
            "has_overdue":         inv.get("overdue_count", 0) > 0,
        }

        deal_id = summary["deal_id"]
        name_key = _normalize(summary["campfire_name"])

        if deal_id:
            by_deal_id[deal_id] = summary
        if name_key:
            name_contracts[name_key].append(summary)

    # Aggregate per normalized name across all contracts for a client
    by_name: dict[str, dict] = {}
    for name_key, summaries in name_contracts.items():
        active = [s for s in summaries if s["status"] == "ACTIVE"]
        by_name[name_key] = {
            "campfire_name":         summaries[0]["campfire_name"],
            "arr":                   round(sum(s["arr"] for s in active), 2),
            "open_invoice_count":    sum(s["open_invoice_count"] for s in summaries),
            "open_amount":           round(sum(s["open_amount"] for s in summaries), 2),
            "days_open":             max((s["days_open"] for s in summaries), default=0),
            "overdue_invoice_count": sum(s["overdue_invoice_count"] for s in summaries),
            "overdue_amount":        round(sum(s["overdue_amount"] for s in summaries), 2),
            "days_overdue":          max((s["days_overdue"] for s in summaries), default=0),
            "has_overdue":           any(s["has_overdue"] for s in summaries),
            "contract_count":        len(summaries),
            "active_contract_count": len(active),
            "contracts":             summaries,
        }

    return {"by_deal_id": by_deal_id, "by_name": by_name}


# ── Legacy shim ────────────────────────────────────────────────────────────────
def get_campfire_arr() -> dict[str, dict]:
    return get_campfire_data()["by_name"]


def get_qualified_campfire_accounts() -> dict[str, dict]:
    """
    Return Campfire clients qualified for the main account list:
      - Has at least one ACTIVE contract, OR
      - Has a non-active contract whose end_date is within the last 12 months (rolling)

    Returns a dict keyed by normalized client name with:
      account_status:   'ACTIVE' | 'CHURNED_RECENT'
      latest_end_date:  most recent contract_end_date across all contracts
      deal_ids:         list of HubSpot deal IDs found on any contract (for reverse lookup)
      arr:              sum of ARR from ACTIVE contracts only
      + all invoice rollup fields (open_invoice_count, overdue_amount, has_overdue, etc.)
    """
    today  = date.today()
    cutoff = today - timedelta(days=365)

    contracts = _fetch_all_contracts()
    invoices  = _fetch_all_invoices()
    inv_index = _build_invoice_index(invoices)

    # Group raw contract summaries by normalized client name
    name_contracts: dict[str, list] = defaultdict(list)
    for raw in contracts:
        if raw.get("is_deleted"):
            continue
        name_key = _normalize(raw.get("client_name", ""))
        if not name_key:
            continue
        contract_id = raw.get("id")
        inv = inv_index.get(contract_id, {})
        name_contracts[name_key].append({
            "campfire_name":       raw.get("client_name", ""),
            "deal_id":             str(raw.get("deal_id") or ""),
            "status":              raw.get("status", ""),
            "arr":                 _annualize(raw),
            "contract_start_date": raw.get("contract_start_date"),
            "contract_end_date":   raw.get("contract_end_date"),
            # Invoice data for this specific contract
            "open_invoice_count":    inv.get("open_count", 0),
            "open_amount":           inv.get("open_amount", 0.0),
            "days_open":             inv.get("days_open", 0),
            "overdue_invoice_count": inv.get("overdue_count", 0),
            "overdue_amount":        inv.get("overdue_amount", 0.0),
            "days_overdue":          inv.get("days_overdue", 0),
            "has_overdue":           inv.get("overdue_count", 0) > 0,
        })

    result: dict[str, dict] = {}
    for name_key, summaries in name_contracts.items():
        active = [s for s in summaries if s["status"] == "ACTIVE"]

        recently_ended = []
        for s in summaries:
            if s["status"] != "ACTIVE":
                end_str = s.get("contract_end_date")
                if end_str:
                    try:
                        if date.fromisoformat(end_str) >= cutoff:
                            recently_ended.append(s)
                    except (ValueError, TypeError):
                        pass

        if not active and not recently_ended:
            continue  # Not qualified — too old or no contracts

        account_status = "ACTIVE" if active else "CHURNED_RECENT"
        all_ends  = [s["contract_end_date"] for s in summaries if s.get("contract_end_date")]
        deal_ids  = list({s["deal_id"] for s in summaries if s.get("deal_id")})

        result[name_key] = {
            "campfire_name":           summaries[0]["campfire_name"],
            "account_status":          account_status,
            "latest_end_date":         max(all_ends) if all_ends else None,
            "deal_ids":                deal_ids,
            # ARR = active contracts only
            "arr":                     round(sum(s["arr"] for s in active), 2),
            # Invoices rolled up across all contracts
            "open_invoice_count":      sum(s["open_invoice_count"]    for s in summaries),
            "open_amount":             round(sum(s["open_amount"]       for s in summaries), 2),
            "days_open":               max((s["days_open"]  for s in summaries), default=0),
            "overdue_invoice_count":   sum(s["overdue_invoice_count"] for s in summaries),
            "overdue_amount":          round(sum(s["overdue_amount"]    for s in summaries), 2),
            "days_overdue":            max((s["days_overdue"] for s in summaries), default=0),
            "has_overdue":             any(s["has_overdue"]   for s in summaries),
            "contract_count":          len(summaries),
            "active_contract_count":   len(active),
        }

    return result


def get_all_contracts_for_retention() -> list[dict]:
    """
    Returns all contracts (all statuses) with fields needed for
    GRR / NRR / Logo Retention analysis. CSM extracted from Employee tag.
    """
    contracts = _fetch_all_contracts()
    result = []
    for c in contracts:
        if c.get("is_deleted"):
            continue
        csm_tag = next(
            (t.get("name") for t in c.get("tags", []) if t.get("group_name") == "Employee"),
            None,
        )
        result.append({
            "contract_id":          c["id"],
            "client_id":            c.get("client"),
            "client_name":          c.get("client_name", ""),
            "deal_id":              str(c.get("deal_id") or ""),
            "deal_name":            c.get("deal_name", ""),
            "contract_start_date":  c.get("contract_start_date"),
            "contract_end_date":    c.get("contract_end_date"),
            "total_contract_value": float(c.get("total_contract_value") or 0),
            "arr":                  _annualize(c),
            "billing_frequency":    c.get("billing_frequency", ""),
            "status":               c.get("status", ""),
            "csm_tag":              csm_tag,
        })
    return result
