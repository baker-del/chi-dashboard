"""
Inspect raw Campfire contract data — statuses, fields, and a sample record.
Run with: python tests/inspect_campfire_contracts.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import requests
import json
from collections import Counter

CAMPFIRE_BASE = "https://api.meetcampfire.com"

def headers():
    key = os.getenv("CAMPFIRE_API_KEY")
    return {"Authorization": f"Token {key}", "Content-Type": "application/json"}

def fetch_all_contracts():
    contracts = []
    url = f"{CAMPFIRE_BASE}/rr/api/v1/contracts"
    params = {"limit": 100}  # No status filter — get everything
    while url:
        r = requests.get(url, headers=headers(), params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        contracts.extend(data.get("results", []))
        url = data.get("next")
        params = {}
        print(f"  fetched {len(contracts)} contracts so far...", end="\r")
    return contracts

print("Fetching all Campfire contracts (no status filter)...")
contracts = fetch_all_contracts()
print(f"\nTotal contracts: {len(contracts)}")

# Status breakdown
statuses = Counter(c.get("status") for c in contracts)
print("\n--- Status breakdown ---")
for status, count in statuses.most_common():
    print(f"  {status}: {count}")

# All available field names
all_keys = set()
for c in contracts:
    all_keys.update(c.keys())
print("\n--- Fields available on contract records ---")
for k in sorted(all_keys):
    print(f"  {k}")

# Sample one contract per status
print("\n--- Sample record per status ---")
seen = set()
for c in contracts:
    s = c.get("status")
    if s not in seen:
        seen.add(s)
        print(f"\n  [{s}]")
        print(json.dumps(c, indent=4, default=str))
