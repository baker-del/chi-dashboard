# CHI 2.0 — ClearlyRated Customer Success Dashboard

## Auto-load instructions
At the start of every session, read SESSION_LOG.md and give the user a brief
briefing: where we left off, what to work on today, and any blockers.

## Project summary
Building a CS leadership dashboard for Eric Gregg (founder, ClearlyRated).
Pulls live data from HubSpot, Campfire, Pendo, and Jira.

## To run the app
```
cd "/Users/egregg/ClearlyRated Dropbox/Eric Gregg/Claude/CXCode/CHI-2.0"
source venv/bin/activate
streamlit run app.py
```

## Key facts
- Python alias in ~/.zshrc points to Homebrew Python 3.13.13
- HubSpot legacy app token is in .env as HUBSPOT_TOKEN
- 649 active customers, 7 CSMs, ~302 open renewal deals
- Renewal date field: cr_next_contract_start2 (NOT cr_next_contract_start)
- Industry field: in_industry_dropdown
- CS Pipeline ID: 10d22554-c166-4c9e-887f-467c6b0b6aa2

## Current build status
- MVP Churn Risk Dashboard: COMPLETE
- Segment 2 (Activity Reporting): IN PROGRESS
- Segment 3 (CHI Health Score): NOT STARTED
- Segment 4 (GRR/NRR): IN PROGRESS — Retention Analytics page built; needs validation against Google Sheet
