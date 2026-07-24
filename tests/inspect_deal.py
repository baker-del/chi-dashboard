"""
Inspect a single deal to see all populated fields.
Usage: python3 tests/inspect_deal.py
"""

import sys, os, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.connectors.hubspot import HEADERS, BASE_URL, get_cs_pipeline_deals

deals = get_cs_pipeline_deals()
print(f"Total open deals: {len(deals)}\n")

# Check how many have cr_next_contract_start2 populated
with_date = [d for d in deals if d.get("properties", {}).get("cr_next_contract_start2")]
with_arr = [d for d in deals if d.get("properties", {}).get("cr_arr")]
print(f"Deals with cr_next_contract_start2 populated: {len(with_date)}")
print(f"Deals with cr_arr populated: {len(with_arr)}\n")

# Show first deal with a date if any exist
if with_date:
    sample = with_date[0]
    props = sample.get("properties", {})
    print(f"Sample deal with date:")
    print(f"  Name: {props.get('dealname')}")
    print(f"  Next Start: {props.get('cr_next_contract_start2')}")
    print(f"  ARR: {props.get('cr_arr')}")
    print(f"  Stage: {props.get('dealstage')}")
else:
    # Fetch one deal directly with all properties to see what's actually there
    deal_id = deals[0]["id"]
    print(f"No deals have cr_next_contract_start2 set.")
    print(f"Fetching deal {deal_id} directly to inspect all fields...\n")
    url = f"{BASE_URL}/crm/v3/objects/deals/{deal_id}"
    params = {"properties": "cr_next_contract_start2,cr_arr,cr_contract_end_date,cr_renewaldate,dealname,dealstage,amount"}
    r = requests.get(url, headers=HEADERS, params=params)
    r.raise_for_status()
    props = r.json().get("properties", {})
    print("All requested fields on this deal:")
    for k, v in props.items():
        print(f"  {k}: {v}")
