"""
Inspect Campfire invoice endpoint fields and a sample record.
Run with: python tests/inspect_campfire_invoices.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

import requests, json

CAMPFIRE_BASE = "https://api.meetcampfire.com"

def headers():
    key = os.getenv("CAMPFIRE_API_KEY")
    return {"Authorization": f"Token {key}", "Content-Type": "application/json"}

# Fetch a small page of invoices
url = f"{CAMPFIRE_BASE}/coa/api/v1/invoice/"
print(f"GET {url}")
r = requests.get(url, headers=headers(), params={"limit": 5, "sort": "-invoice_date"}, timeout=30)
print(f"Status: {r.status_code}\n")

if r.status_code != 200:
    print(r.text[:500])
    sys.exit(1)

data = r.json()
results = data.get("results", data if isinstance(data, list) else [])
print(f"Total invoices (approx): {data.get('count', 'unknown')}")
print(f"Returned: {len(results)}\n")

if not results:
    print("No results.")
    sys.exit(0)

print("--- Fields ---")
for k in sorted(results[0].keys()):
    print(f"  {k}: {results[0][k]!r}")

print("\n--- Full sample invoice ---")
print(json.dumps(results[0], indent=2, default=str))

# Also try filtering by outstanding/unpaid
print("\n--- Unpaid invoices (status filter) ---")
r2 = requests.get(url, headers=headers(),
                  params={"limit": 3, "status": "UNPAID", "sort": "-invoice_date"}, timeout=30)
print(f"Status filter UNPAID: {r2.status_code}, count={r2.json().get('count', '?')}")

r3 = requests.get(url, headers=headers(),
                  params={"limit": 3, "status": "OUTSTANDING", "sort": "-invoice_date"}, timeout=30)
print(f"Status filter OUTSTANDING: {r3.status_code}, count={r3.json().get('count', '?')}")
