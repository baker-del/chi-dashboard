"""
Run this script to verify your HubSpot connection is working.
Usage: python3 tests/test_hubspot_connection.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.connectors.hubspot import (
    get_owners, get_pipelines, get_csm_owner_ids, get_active_customers,
    get_cs_pipeline_deals, build_company_deal_map
)


def test_connection():
    print("\n=== CHI 2.0 — HubSpot Connection Test ===\n")

    # Test 1: CSM team members
    print("1. Fetching CSM team owner IDs...")
    try:
        csm_ids = get_csm_owner_ids()
        print(f"   ✅ Found {len(csm_ids)} CSM team members (owner IDs: {csm_ids})")
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        csm_ids = []

    # Test 2: Active customers filtered by lifecycle stage + CSM team
    print("\n2. Fetching active customers (lifecycle = customer, owned by CSM team)...")
    try:
        customers = get_active_customers(csm_owner_ids=csm_ids if csm_ids else None)
        print(f"   ✅ Found {len(customers)} active customers")
        print(f"\n   Sample (first 5):")
        for c in customers[:5]:
            props = c.get("properties", {})
            print(f"      - {props.get('name', 'Unnamed')} | "
                  f"Risk: {props.get('cr_churnrisk', 'not set')} | "
                  f"Tier: {props.get('cr_tier_company', 'not set')}")
    except Exception as e:
        print(f"   ❌ Failed: {e}")

    # Test 3: Spot check — find a company with churn risk set
    print("\n3. Looking for accounts with churn risk set...")
    try:
        all_customers = get_active_customers(csm_owner_ids=csm_ids if csm_ids else None)
        at_risk = [
            c for c in all_customers
            if c.get("properties", {}).get("cr_churnrisk") not in (None, "1 - very low risk", "")
        ]
        print(f"   ✅ Found {len(at_risk)} accounts with elevated churn risk")
        for c in at_risk[:5]:
            props = c.get("properties", {})
            print(f"      - {props.get('name', 'Unnamed')} | "
                  f"Risk: {props.get('cr_churnrisk')} | "
                  f"Reasons: {props.get('cr_churn_risk_reasons', 'none')}")
    except Exception as e:
        print(f"   ❌ Failed: {e}")

    # Test 4: CS Pipeline deals with renewal dates
    print("\n4. Fetching CS Pipeline deals (renewals)...")
    try:
        deals = get_cs_pipeline_deals()
        deal_map = build_company_deal_map(deals)
        print(f"   ✅ Found {len(deals)} deals | {len(deal_map)} linked to companies")
        for deal in deals[:5]:
            props = deal.get("properties", {})
            print(f"      - {props.get('dealname', 'Unnamed')} | "
                  f"Stage: {props.get('dealstage')} | "
                  f"Next Start: {props.get('cr_next_contract_start2', 'not set')} | "
                  f"ARR: ${props.get('cr_arr', props.get('amount', '0'))}")
    except Exception as e:
        print(f"   ❌ Failed: {e}")

    print("\n=== Test complete ===\n")


if __name__ == "__main__":
    test_connection()
