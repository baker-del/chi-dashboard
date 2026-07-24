# CHI 2.0 — Customer Success Intelligence Dashboard
## Project Framework & Plan
*Last updated: 2026-05-04*

---

## What We're Building

A web-based dashboard that gives the ClearlyRated CS leadership team real-time visibility into:
- **How the business is retaining and growing revenue** (Segment 1: Retention)
- **How active and responsive the CS team is** (Segment 2: Activity)
- **How healthy each customer account is** (Segment 3: CHI / Health Scoring)

The app pulls live data from HubSpot, Pilot, Campfire, and Pendo. It runs locally first, then deploys to Google Cloud.

---

## Three Build Segments

### Segment 1: Retention Reporting
**Goal:** Know your numbers — past, present, and projected.

Key metrics:
- Logo Retention Rate (% of customers retained)
- Gross Revenue Retention (GRR) — revenue kept, ignoring expansions
- Net Revenue Retention (NRR) — revenue after churn + expansions
- Expansion Revenue — upsells and cross-sells
- At-Risk ARR — revenue tied to flagged accounts

Views needed:
- Company-wide, by time period (monthly, quarterly, annual)
- By CSM (each rep's book of business)
- Forward-looking: renewal pipeline with probability weighting
- Historical: how did we do in Q1? vs. Q4?

---

### Segment 2: Activity Reporting
**Goal:** Know what your team is actually doing.

Key metrics:
- Average email response time (HubSpot tracked emails)
- Number of meetings per CSM per period
- Customers with no contact in last 30 / 60 / 90 days (configurable)
- Total touchpoints per customer

Views needed:
- By CSM — are they staying engaged with their book?
- By customer — when did we last touch this account?
- Leaderboard / comparison across team

---

### Segment 3: CHI Management (Customer Health Index)
**Goal:** Know which accounts are healthy, drifting, or at risk.

Initial health score inputs (extensible):
- Product logins (Pendo) — frequency and recency
- Survey scheduled / active (Pilot/ClearlyRated platform)
- Days since last email or meeting (HubSpot)

Additional inputs to add over time:
- NPS/CSAT score trends
- Support ticket volume
- Contract renewal proximity
- Expansion/contraction signals

Features:
- Color-coded health score per account (Green / Yellow / Red)
- Drill-down per account: why is the score what it is?
- By-CSM rollup: what does their book look like?
- Automated email digest: weekly (or on-demand) email to each CSM flagging new risks and upcoming renewals

---

## Technical Architecture (Proposed)

```
[Data Sources]          [Backend]           [Frontend]
HubSpot API      -->    
Campfire API     -->    Python/FastAPI   --> React Dashboard
Pilot API        -->    (local first,    
Pendo API        -->    then GCP)        
```

**Why this stack:**
- Python is readable, widely supported, and has strong API libraries
- FastAPI is lightweight and easy to maintain without deep dev experience
- React gives us a clean, interactive dashboard
- This stack deploys cleanly to Google Cloud Run (ClearlyRated's likely hosting)

**Database:** PostgreSQL (store snapshots of data so reports are fast and we have history)

**Scheduling:** A simple job runner (APScheduler) to pull fresh data from APIs on a schedule (e.g., every hour or nightly)

---

## Build Sequence & Time Estimates

| Phase | Work | Est. Time |
|-------|------|-----------|
| 0 | Setup: project scaffold, API connections, local environment | 1–2 days |
| 1A | HubSpot + Campfire data pull & retention calculations | 3–4 days |
| 1B | Retention dashboard UI (company + CSM views) | 2–3 days |
| 1C | Forward-looking pipeline / renewal modeling | 2–3 days |
| **S1 Total** | **Segment 1 complete + tested** | **~2 weeks** |
| 2A | Activity data pull (emails, meetings, last-contact) | 2–3 days |
| 2B | Activity dashboard UI | 2 days |
| **S2 Total** | **Segment 2 complete + tested** | **~1 week** |
| 3A | Pendo + health score engine | 3–4 days |
| 3B | CHI dashboard (account + CSM views) | 2–3 days |
| 3C | Automated email digest to CSMs | 2 days |
| **S3 Total** | **Segment 3 complete + tested** | **~2 weeks** |
| 4 | Google Cloud deployment + final QA | 3–5 days |
| **Total** | | **~6–7 weeks** |

*Note: These estimates assume 2–4 hours of active work per day and account time for API credential setup, testing, and iteration.*

---

## Token Efficiency Strategy

Since we're on a $20/month Claude plan, here's how we stay efficient:

1. **Session-based work** — Each session focuses on ONE task (e.g., "build the HubSpot data connector"). Start each session with the specific file(s) in context, not the whole project.

2. **Checkpointing** — At the end of each session, we write a brief `SESSION_LOG.md` with what was done and what's next. That's your resume point.

3. **Compact prompts** — Instead of explaining the whole project each time, we'll say "continue from SESSION_LOG.md" and Claude reads just that file.

4. **Segment isolation** — Each segment is its own folder. We only load what's relevant to the current segment.

5. **Test-as-you-go** — Write small test scripts per function so bugs are caught early (cheap to fix) rather than late (expensive to debug across the whole system).

6. **Overnight runs** — Claude Code can run longer tasks (API setup, data schema creation) overnight. We'll set up a task queue pattern so you can kick off work before bed.

---

## Clarifying Questions (Open)

*See CLARIFYING_QUESTIONS.md for the full list that needs answers before each segment begins.*

---

## Files in This Project

```
CHI-2.0/
├── PROJECT_FRAMEWORK.md     ← This file
├── CLARIFYING_QUESTIONS.md  ← Questions needing answers
├── SESSION_LOG.md           ← Running log of what's been built
├── docs/                    ← API docs, data dictionaries
├── backend/                 ← Python/FastAPI app
├── frontend/                ← React dashboard
├── tests/                   ← Test suite
└── deploy/                  ← Google Cloud deployment config
```
