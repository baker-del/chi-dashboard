import time
import requests
from backend.config import HUBSPOT_TOKEN

BASE_URL = "https://api.hubapi.com"

HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_TOKEN}",
    "Content-Type": "application/json",
}


def hs_request(method, url, **kwargs):
    """requests wrapper with retry on 429, 5xx, and connection drops."""
    for attempt in range(5):
        try:
            r = getattr(requests, method)(url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as e:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise
        if r.ok:
            return r
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 10))
            time.sleep(wait)
        elif r.status_code in (502, 503, 504) and attempt < 4:
            time.sleep(2 ** attempt)
        else:
            r.raise_for_status()
    r.raise_for_status()
    return r

COMPANY_PROPERTIES = [
    "name",
    "hubspot_owner_id",
    "lifecyclestage",
    "cr_churnrisk",
    "cr_churn_risk_reasons",
    "cr_tier_company",
    "in_industry_dropdown",
]

DEAL_PROPERTIES = [
    "dealname",
    "dealstage",
    "pipeline",
    "amount",
    "closedate",
    "hubspot_owner_id",
    "cr_next_contract_start2",
    "cr_contract_end_date",
    "cr_arr",
    "cr_expansiontype",
    "cr_tierdeal",
]

# Pipeline and stage IDs from HubSpot
CS_PIPELINE_ID = "10d22554-c166-4c9e-887f-467c6b0b6aa2"
CS_STAGE_CLOSED_WON = "16515"
CS_STAGE_CLOSED_LOST = "16516"

EXPANSION_PIPELINE_ID = "47062345"
EXPANSION_STAGE_CLOSED_WON = "96961410"

CS_STAGE_NAMES = {
    "927becf5-85f5-465b-ae35-c305ce67395e": "Upcoming Renewal",
    "7ef874ea-459f-4a69-924a-dad392ce886c": "Account Review (In Progress)",
    "16512": "Account Review Completed",
    "16513": "Contract Sent",
    "16514": "Verbally Accepted",
    "16515": "Renewed",
    "16516": "Churned",
}


def get_all_pages(url, params):
    """Fetch all pages from a HubSpot list endpoint."""
    results = []
    after = None

    while True:
        if after:
            params["after"] = after

        response = hs_request("get", url, headers=HEADERS, params=params)
        response.raise_for_status()
        data = response.json()

        results.extend(data.get("results", []))

        paging = data.get("paging", {})
        next_page = paging.get("next", {})
        after = next_page.get("after")

        if not after:
            break

    return results


# Fallback CSM owner IDs — used if the HubSpot teams API is unavailable
CSM_OWNER_IDS_FALLBACK = [
    "80047394",   # Zach Panos
    "70636304",   # Zach Hankin
    "112348477",  # Amber Moreno
    "80231647",   # Scheri Smith
    "117832540",  # Kirsten Handal
    "86828234",   # Bernardo Mattos
    "87488670",   # Jenni Schwittay
    "30109013",   # Eric Gregg
]


def get_csm_owner_ids():
    """
    Get owner IDs for the CSM team from HubSpot.
    Requires settings.users.teams.read scope; falls back to hardcoded list.
    Note: teams API returns userId values, not owner IDs — must map through owners.
    """
    try:
        teams_resp = hs_request("get", f"{BASE_URL}/settings/v3/users/teams", headers=HEADERS)
        teams_resp.raise_for_status()
        csm_user_ids = set()
        for team in teams_resp.json().get("results", []):
            if team.get("name", "").strip().lower() == "csm":
                csm_user_ids.update(str(uid) for uid in team.get("userIds", []))

        if not csm_user_ids:
            return CSM_OWNER_IDS_FALLBACK

        # Map HubSpot userIds → owner IDs (they are different fields)
        owners_resp = hs_request("get", f"{BASE_URL}/crm/v3/owners/", headers=HEADERS, params={"limit": 100})
        owners_resp.raise_for_status()
        owner_ids = [
            str(o["id"]) for o in owners_resp.json().get("results", [])
            if str(o.get("userId", "")) in csm_user_ids
        ]
        if not owner_ids:
            return CSM_OWNER_IDS_FALLBACK
        # Always include Eric Gregg (owner 30109013) — he manages the team but
        # his HubSpot userId isn't in the CSM team roster
        if "30109013" not in owner_ids:
            owner_ids.append("30109013")
        return owner_ids

    except Exception:
        return CSM_OWNER_IDS_FALLBACK


def get_active_customers(csm_owner_ids=None):
    """Fetch all active customers (lifecyclestage = customer), optionally filtered by CSM team."""
    url = f"{BASE_URL}/crm/v3/objects/companies/search"

    filters = [
        {
            "propertyName": "lifecyclestage",
            "operator": "EQ",
            "value": "customer",
        }
    ]

    if csm_owner_ids:
        filters.append({
            "propertyName": "hubspot_owner_id",
            "operator": "IN",
            "values": [str(i) for i in csm_owner_ids],
        })

    payload = {
        "filterGroups": [{"filters": filters}],
        "properties": COMPANY_PROPERTIES,
        "limit": 100,
    }

    results = []
    after = None

    while True:
        if after:
            payload["after"] = after

        response = hs_request("post", url, headers=HEADERS, json=payload)
        response.raise_for_status()
        data = response.json()

        results.extend(data.get("results", []))

        paging = data.get("paging", {})
        after = paging.get("next", {}).get("after")

        if not after:
            break

    return results


def get_all_customers_full():
    """
    Fetch ALL HubSpot companies with lifecyclestage=customer (no CSM filter).
    Returns list of {id, name, owner_id} dicts — used for data health checks.
    """
    return _fetch_hs_companies(lifecycle_stages=["customer"])


def get_all_hs_companies_for_mapping():
    """
    Fetch ALL HubSpot companies regardless of lifecycle stage — for mapping dropdowns.
    Includes current customers, former customers, churned, etc.
    Returns sorted list of company names.
    """
    return _fetch_hs_companies(lifecycle_stages=None)


def _fetch_hs_companies(lifecycle_stages=None):
    """
    Fetch HubSpot companies, optionally filtered to specific lifecycle stages.
    lifecycle_stages=None → all companies (no lifecycle filter).
    Returns list of {id, name, owner_id} dicts.
    """
    url = f"{BASE_URL}/crm/v3/objects/companies/search"
    filters = []
    if lifecycle_stages:
        if len(lifecycle_stages) == 1:
            filters.append({"propertyName": "lifecyclestage", "operator": "EQ", "value": lifecycle_stages[0]})
        else:
            filters.append({"propertyName": "lifecyclestage", "operator": "IN", "values": lifecycle_stages})

    payload = {
        "properties": ["name", "hubspot_owner_id", "lifecyclestage"],
        "limit": 100,
    }
    if filters:
        payload["filterGroups"] = [{"filters": filters}]
    results = []
    after = None
    while True:
        if after:
            payload["after"] = after
        response = hs_request("post", url, headers=HEADERS, json=payload)
        response.raise_for_status()
        data = response.json()
        for r in data.get("results", []):
            p = r.get("properties", {})
            name = p.get("name") or ""
            if not name:
                continue
            results.append({
                "id":         r["id"],
                "name":       name,
                "owner_id":   p.get("hubspot_owner_id") or "",
                "lifecycle":  p.get("lifecyclestage") or "",
            })
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return results


def get_cs_pipeline_deals():
    """Fetch all open (non-closed) deals in the CS Pipeline."""
    url = f"{BASE_URL}/crm/v3/objects/deals/search"
    payload = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "pipeline",
                        "operator": "EQ",
                        "value": CS_PIPELINE_ID,
                    },
                    {
                        "propertyName": "dealstage",
                        "operator": "NOT_IN",
                        "values": [CS_STAGE_CLOSED_WON, CS_STAGE_CLOSED_LOST],
                    },
                ]
            }
        ],
        "properties": DEAL_PROPERTIES,
        "limit": 100,
    }

    results = []
    after = None

    while True:
        if after:
            payload["after"] = after

        response = hs_request("post", url, headers=HEADERS, json=payload)
        response.raise_for_status()
        data = response.json()

        results.extend(data.get("results", []))

        paging = data.get("paging", {})
        after = paging.get("next", {}).get("after")

        if not after:
            break

    return results


def get_deal_company_associations(deal_ids):
    """Batch-fetch company associations for a list of deal IDs."""
    url = f"{BASE_URL}/crm/v3/associations/deals/companies/batch/read"
    deal_to_company = {}

    # HubSpot batch limit is 100
    for i in range(0, len(deal_ids), 100):
        batch = deal_ids[i:i + 100]
        payload = {"inputs": [{"id": str(d)} for d in batch]}
        response = hs_request("post", url, headers=HEADERS, json=payload)
        response.raise_for_status()
        results = response.json().get("results", [])
        for item in results:
            deal_id = str(item.get("from", {}).get("id"))
            to_list = item.get("to", [])
            if to_list:
                deal_to_company[deal_id] = str(to_list[0].get("id"))

    return deal_to_company


def build_company_deal_map(deals):
    """Return a dict mapping company_id -> deal for quick lookup."""
    deal_ids = [d["id"] for d in deals]
    deal_to_company = get_deal_company_associations(deal_ids)

    company_deal_map = {}
    for deal in deals:
        company_id = deal_to_company.get(str(deal["id"]))
        if company_id:
            company_deal_map[company_id] = deal

    return company_deal_map


def get_owners():
    """Fetch all HubSpot owners (CSMs and other users)."""
    url = f"{BASE_URL}/crm/v3/owners/"
    response = hs_request("get", url, headers=HEADERS)
    response.raise_for_status()
    return response.json().get("results", [])


def get_pipelines():
    """Fetch all deal pipelines and their stages — useful for verifying pipeline IDs."""
    url = f"{BASE_URL}/crm/v3/pipelines/deals"
    response = hs_request("get", url, headers=HEADERS)
    response.raise_for_status()
    return response.json().get("results", [])
