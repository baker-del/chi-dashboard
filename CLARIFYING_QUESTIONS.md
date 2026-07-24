# Clarifying Questions — CHI 2.0

*Answers needed before each segment can be fully scoped. Priority marked [BLOCKING] if we can't start without it.*

---

## Section A: Data & APIs — [BLOCKING for Phase 0]

### HubSpot
1. ✅ Do you have a HubSpot API key or access to create one? **Yes — has admin access. Creating a new legacy app. Baker (CEO) confirmed this approach.**
2. ✅ Which HubSpot objects do you use for customers? **Companies = customer accounts. Contacts roll up to Companies. Deals are associated to Companies.**
3. ✅ How are renewals tracked in HubSpot? **CS Pipeline (renewals) and Expansions Pipeline (expansions). Key renewal date field: CR_Next Contract Start.**
4. ✅ Are emails logged automatically? **Yes — Google Workspace connected to HubSpot, emails auto-logged.**
5. ✅ Are meetings logged automatically? **Yes — Google Calendar connected to HubSpot, meetings auto-logged.**
6. ✅ Standard renewal date field? **CR_Next Contract Start — counts revenue when next contract starts, not when deal closes.**
7. ⬜ How many total active customer accounts? *(still needed)*
8. ⬜ How many CSMs are on the team? *(still needed)*

**HubSpot App Scopes needed for new legacy app:**
- `crm.objects.contacts.read`
- `crm.objects.companies.read`
- `crm.objects.deals.read`
- `crm.schemas.deals.read`
- `crm.objects.owners.read`
- `crm.objects.meetings.read`
- `crm.objects.engagements.read`
- `sales-email-read`
- `crm.objects.forecasts.read`

**CS Pipeline stages:** Upcoming Renewal → Account Review (In Progress) → Account Review Completed → Contract Sent → Verbally Accepted → **Renewed (Closed Won)** / **Churned (Closed Lost)**

**Expansions Pipeline:** Closed Won at signing. Expansion type stored in `CR_expansionType` property.

**Expansion matching rule:** Match expansion deals to renewals where close date is within ±3 weeks of CR_Next Contract Start.

### Campfire (Financial Reporting)
9. ✅ SaaS or internal? **SaaS product — campfire.ai**
10. ✅ Has API? **Yes — REST API with 100+ endpoints. Docs at docs.campfire.ai. Static API key auth.**
11. ✅ What does it store? **ARR by account, by contract, invoices — full accounting suite.**
12. ✅ ARR source of truth? **Campfire. Data flow: HubSpot deals → Campfire (input), Campfire ARR → HubSpot (writes back). Read ARR from Campfire directly.**
- ⬜ **Action:** Get Campfire API key from admin settings.

### Pilot / CFT (Proprietary Survey Platform)
13. ✅ What does Pilot manage? **Internal backend for ClearlyRated survey product. CFT is a second platform. Both merging into one.**
14. ✅ API access? **Parked — platforms are merging. Will add when ready.**
15. ✅ What data from Pilot? **Survey schedules, account status — to be added to health score in a later phase.**

### Pendo
16. ✅ Admin access? **No direct admin access — can get API key from someone who does.**
17. ✅ What product is tracked? **Main ClearlyRated survey portal (Pilot side). CFT does not have Pendo.**
18. ✅ What data matters? **Login frequency + recency both matter for health scoring.**
- ⬜ **Action:** Get Pendo API key.

### Jira (Added)
- ✅ Support and engineering tickets tracked in Jira — useful as a health score signal (high ticket volume = friction/churn risk)
- ⬜ **Action:** Confirm Jira API access and project key when ready for Segment 3.

---

## Section B: Metrics Definitions

### Revenue Metrics
19. ✅ Churn definition? **Early cancellations and non-renewals treated the same.**
20. ✅ GRR/NRR measurement? **Renewal-cohort based: denominator = total ARR up for renewal in period (by CR_Next Contract Start from Campfire). Numerator = % of prior contract recurring revenue captured at renewal.**
21. ✅ What counts as expansion? **Price increases at renewal count as expansions. Currently tracked in Expansions pipeline separately. Will change in future — build to be adaptable.**
22. ✅ Contract length? **Typically annual. Some multi-year. Rarely monthly/quarterly.**
23. ✅ Pricing structure? **Flat annual fee based on firm size, survey campaigns, features. Don't need to capture criteria in first build — just contract total value and expansion value.**
24. ✅ Fiscal year? **Calendar year Jan–Dec. Need both backward and forward calculations.**
25. ✅ Existing GRR/NRR calc? **Yes — in a Google Doc spreadsheet. Use for cross-check validation.**

### At-Risk / Churn Risk
26. ✅ At-risk definition? **CSM judgment, tracked in HubSpot as CR_ChurnRisk (dropdown) and CR_Churn Risk Reasons (multi-select dropdown).**
27. ✅ Tracked where? **HubSpot company properties.**

**CR_ChurnRisk values:**
- 1 — Very Low Risk
- 2 — Somewhat Low Risk
- 3 — Neutral
- 4 — Somewhat High Risk
- 5 — Very High Risk
- 6 — Churned

**CR_Churn Risk Reasons values (multi-select):**
- Low/No surveys sent
- M&A
- Low engagement
- Turnover of key contact
- Strained relationship
- Product related issue
- Support related issue
- Response rate issues
- Internal priorities have changed
- Change in CSM relationship

---

## 🔄 REVISED BUILD ORDER (updated 2026-05-04)

GRR/NRR calculations already exist elsewhere. Build churn risk MVP first for immediate value.

| Phase | What | Status |
|-------|------|--------|
| **MVP** | Churn Risk Dashboard | 🔜 Ready to build once HubSpot app created |
| **2** | Activity Reporting | Waiting on HubSpot scopes |
| **3** | CHI Health Score | Waiting on Pendo + Jira API keys |
| **4** | GRR / NRR Calculations | Lower priority — already working elsewhere |

---

## Section C: Activity Reporting — [BLOCKING for Segment 2]

28. ⬜ What email response time target do you have for CSMs?
29. ⬜ How many meetings per month should a CSM be having with customers?
30. ⬜ Is "a meeting" defined as any HubSpot meeting, or only external customer meetings?
31. ⬜ What is your expected contact cadence per customer?
32. ⬜ Do you have customer tiers? Should activity thresholds differ by tier?

---

## Section D: CHI Health Score — [BLOCKING for Segment 3]

33. ⬜ How would you weight these health factors (must add to 10)?
    - Product logins / Pendo engagement
    - Survey scheduled and active
    - Recency of email or meeting contact
    - Jira ticket volume (newly added)
34. ⬜ What does "healthy" look like for each factor?
35. ⬜ Should health thresholds differ by customer tier or contract size?
36. ⬜ Who receives the automated email digest?
37. ⬜ How often should the digest go out?
38. ⬜ Should the digest include suggested actions or just data?

---

## Section E: Access & Deployment

39. ⬜ What computer/OS will you run this on locally?
40. ⬜ Do you have Python installed?
41. ⬜ Google Cloud setup — Cloud Run, App Engine, or other?
42. ⬜ Who maintains this after launch?
43. ⬜ Should CSMs be able to log in, or leadership-only for now?

---

## Section F: Nice-to-Haves

44. ⬜ Other tools to add later? (Slack alerts, etc.)
45. ⬜ Design preference — minimal or data-dense?
46. ⬜ Mobile access needed, or desktop-only?
47. ⬜ Compliance requirements (GDPR, SOC 2)?

---

*MVP (Churn Risk Dashboard) can begin as soon as HubSpot legacy app is created and token is available.*
