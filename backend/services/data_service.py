from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from backend.connectors.hubspot import (
    get_active_customers,
    get_cs_pipeline_deals,
    build_company_deal_map,
    get_owners,
    get_csm_owner_ids,
    CS_STAGE_NAMES,
)
from backend.connectors.hubspot_activity import get_company_email_stats, get_company_meeting_stats
from backend.connectors.pendo import get_pendo_account_stats, _normalize as pendo_normalize
from backend.connectors.campfire import (
    get_qualified_campfire_accounts,
    _normalize as campfire_normalize,
    load_mappings,
)
from backend.connectors.news import get_news_cached_only
from backend.services.chi_score import calculate_chi


def parse_risk_score(risk_str):
    if not risk_str:
        return 0
    try:
        return int(risk_str.split(" - ")[0])
    except (ValueError, IndexError):
        return 0


def days_until(date_str):
    if not date_str:
        return None
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (target - date.today()).days
    except ValueError:
        return None


def build_owner_map(owners):
    return {
        str(o["id"]): f"{o.get('firstName', '')} {o.get('lastName', '')}".strip()
        for o in owners
    }


def get_dashboard_data(email_stats=None):
    owners    = get_owners()
    owner_map = build_owner_map(owners)
    csm_ids   = get_csm_owner_ids()

    # HubSpot data — loaded for enrichment (CSM, risk, deals, activity)
    customers      = get_active_customers(csm_owner_ids=csm_ids)
    deals          = get_cs_pipeline_deals()
    company_deal_map = build_company_deal_map(deals)
    cf_manual      = load_mappings()
    cf_mapped      = cf_manual.get("mappings", {})       # {hs_name: cf_name_or_list}
    cf_disregarded_hs = set(cf_manual.get("disregarded_hubspot", []))

    if email_stats is None:
        with ThreadPoolExecutor(max_workers=4) as pool:
            f_email    = pool.submit(get_company_email_stats,           days=90, owner_ids=csm_ids)
            f_meeting  = pool.submit(get_company_meeting_stats,         days=90, owner_ids=csm_ids)
            f_pendo    = pool.submit(get_pendo_account_stats)
            f_campfire = pool.submit(get_qualified_campfire_accounts)
            email_stats   = f_email.result()
            meeting_stats = f_meeting.result()
            pendo_stats   = f_pendo.result()
            cf_qualified  = f_campfire.result()
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_meeting  = pool.submit(get_company_meeting_stats,         days=90, owner_ids=csm_ids)
            f_pendo    = pool.submit(get_pendo_account_stats)
            f_campfire = pool.submit(get_qualified_campfire_accounts)
            meeting_stats = f_meeting.result()
            pendo_stats   = f_pendo.result()
            cf_qualified  = f_campfire.result()

    # ── Build HubSpot lookup indices for enrichment ────────────────────────────
    # hs_by_norm_name:  campfire_normalize(hs_name) → company object
    # hs_by_deal_id:    hs_deal_id (str)            → company object
    hs_by_norm_name: dict[str, dict] = {}
    hs_by_deal_id:   dict[str, dict] = {}
    hs_by_id:        dict[str, dict] = {}

    for company in customers:
        props      = company.get("properties", {})
        name       = props.get("name") or ""
        company_id = company.get("id")
        hs_by_id[company_id] = company
        norm = campfire_normalize(name)
        if norm:
            hs_by_norm_name[norm] = company
        deal = company_deal_map.get(company_id)
        if deal:
            hs_by_deal_id[str(deal.get("id") or "")] = company

    # Reverse of manual mappings: campfire_normalize(cf_name) → hs_name
    cf_norm_to_hs: dict[str, str] = {}
    for hs_name, cf_name_or_list in cf_mapped.items():
        names = cf_name_or_list if isinstance(cf_name_or_list, list) else [cf_name_or_list]
        for n in names:
            cf_norm_to_hs[campfire_normalize(n)] = hs_name

    def _find_hs_company(cf_name_key: str, cf_record: dict):
        """Find the matching HubSpot company for a Campfire account."""
        # 1. Direct deal_id link (most reliable)
        for did in cf_record.get("deal_ids", []):
            if did and did in hs_by_deal_id:
                return hs_by_deal_id[did]
        # 2. Manual mapping reverse
        if cf_name_key in cf_norm_to_hs:
            mapped_hs_name = cf_norm_to_hs[cf_name_key]
            hs_norm = campfire_normalize(mapped_hs_name)
            if hs_norm in hs_by_norm_name:
                return hs_by_norm_name[hs_norm]
        # 3. Normalized name fallback
        if cf_name_key in hs_by_norm_name:
            return hs_by_norm_name[cf_name_key]
        return None

    accounts = []
    hs_matched_ids: set[str] = set()

    # ── Primary loop: one account per Campfire qualified client ───────────────
    for cf_name_key, cf in cf_qualified.items():
        hs_company = _find_hs_company(cf_name_key, cf)
        if hs_company:
            hs_matched_ids.add(hs_company.get("id"))

        props      = hs_company.get("properties", {}) if hs_company else {}
        company_id = hs_company.get("id")            if hs_company else None

        risk_str   = props.get("cr_churnrisk") or ""
        risk_score = parse_risk_score(risk_str)
        risk_label = risk_str.split(" - ", 1)[1].title() if " - " in risk_str else "Not Set"
        reasons_raw = props.get("cr_churn_risk_reasons") or ""
        reasons = [r.strip() for r in reasons_raw.split(";") if r.strip()] if reasons_raw else []

        owner_id   = str(props.get("hubspot_owner_id") or "")
        owner_name = owner_map.get(owner_id, "Unassigned")

        deal = company_deal_map.get(company_id) if company_id else None
        if deal:
            deal_props      = deal.get("properties", {})
            deal_id         = deal.get("id")
            renewal_date    = deal_props.get("cr_next_contract_start2")
            arr_deal        = deal_props.get("cr_arr")
            try:
                arr_deal = float(arr_deal) if arr_deal else None
            except ValueError:
                arr_deal = None
            stage_id        = deal_props.get("dealstage", "")
            stage_name      = CS_STAGE_NAMES.get(stage_id, stage_id)
            days_to_renewal = days_until(renewal_date)
        else:
            deal_id = renewal_date = arr_deal = None
            stage_name      = "No Active Deal"
            days_to_renewal = None

        no_active_deal = deal is None

        email_data    = (email_stats or {}).get(company_id, {}) if company_id else {}
        meeting_data  = meeting_stats.get(company_id, {})       if company_id else {}
        last_email    = email_data.get("last_email_date")
        last_outbound = email_data.get("last_outbound_date")
        last_meeting  = meeting_data.get("last_meeting_date")
        now           = datetime.now(timezone.utc)

        # Pendo — try Campfire name first, then HubSpot name
        cf_display_name = cf["campfire_name"]
        hs_name         = props.get("name") or ""
        pendo_data      = pendo_stats.get(pendo_normalize(cf_display_name), {})
        if not pendo_data and hs_name:
            pendo_data  = pendo_stats.get(pendo_normalize(hs_name), {})
        pendo_lv = pendo_data.get("lastvisit_dt")
        pendo_days_since_login = (now - pendo_lv).days if pendo_lv else None

        days_since_email    = (now - last_email).days    if last_email    else None
        days_since_outbound = (now - last_outbound).days if last_outbound else None
        days_since_meeting  = (now - last_meeting).days  if last_meeting  else None
        contact_dates = [d for d in [last_email, last_meeting] if d is not None]
        last_contact  = max(contact_dates) if contact_dates else None
        days_since_last_contact = (now - last_contact).days if last_contact else None

        email_count   = email_data.get("email_count", 0)
        meeting_count = meeting_data.get("meeting_count", 0)

        _chi = calculate_chi({
            "risk_score":              risk_score,
            "pendo_days_since_login":  pendo_days_since_login,
            "days_since_last_contact": days_since_last_contact,
            "campfire_days_overdue":   cf.get("days_overdue") or None,
            "days_to_renewal":         days_to_renewal,
        })

        news_articles = get_news_cached_only(cf_display_name or hs_name)

        accounts.append({
            "id":          company_id or f"cf_{cf_name_key}",
            "name":        cf_display_name or hs_name or "Unknown",
            "owner_name":  owner_name,
            "owner_id":    owner_id,
            "risk_score":  risk_score,
            "risk_label":  risk_label,
            "risk_reasons": ", ".join(reasons) if reasons else "—",
            "tier":        props.get("cr_tier_company") or "—",
            "industry":    props.get("in_industry_dropdown") or "—",
            "renewal_date":    renewal_date or "—",
            "days_to_renewal": days_to_renewal,
            "arr":             arr_deal,
            "deal_stage":      stage_name,
            "possibly_stale":  no_active_deal,
            "email_count_90d":           email_count,
            "outbound_count_90d":        email_data.get("outbound_count", 0),
            "inbound_count_90d":         email_data.get("inbound_count", 0),
            "days_since_any_email":      days_since_email,
            "days_since_outbound_email": days_since_outbound,
            "meeting_count_90d":         meeting_count,
            "days_since_meeting":        days_since_meeting,
            "days_since_last_contact":   days_since_last_contact,
            "total_touchpoints_90d":     email_count + meeting_count,
            "median_response_minutes":   email_data.get("median_response_minutes"),
            "response_times":            email_data.get("response_times", []),
            "pendo_days_since_login":    pendo_days_since_login,
            "pendo_visitor_ts":          pendo_data.get("visitor_lastvisits_ms", []),
            # Campfire fields (always present — Campfire is source of truth)
            "campfire_arr":                   cf.get("arr"),
            "campfire_account_status":        cf.get("account_status", "ACTIVE"),
            "campfire_open_invoice_count":    cf.get("open_invoice_count", 0),
            "campfire_open_amount":           cf.get("open_amount"),
            "campfire_days_open":             cf.get("days_open") or None,
            "campfire_has_overdue":           cf.get("has_overdue", False),
            "campfire_overdue_invoice_count": cf.get("overdue_invoice_count", 0),
            "campfire_overdue_amount":        cf.get("overdue_amount"),
            "campfire_days_overdue":          cf.get("days_overdue") or None,
            "campfire_contract_end":          cf.get("latest_end_date"),
            "campfire_contract_start":        None,
            # Source flag — True = from Campfire qualified list
            "campfire_source":   True,
            "campfire_only":     hs_company is None,
            # CHI
            "chi_score":     _chi["chi_score"],
            "chi_color":     _chi["chi_color"],
            "chi_label":     _chi["chi_label"],
            "chi_breakdown": _chi["chi_breakdown"],
            # News
            "news_articles": news_articles,
            "news_count":    len(news_articles),
        })

    # ── Audit list: HubSpot customers with no Campfire match ──────────────────
    # These stay in the returned list (campfire_source=False) so app.py can show
    # them in the audit expander without a separate data fetch.
    for company in customers:
        company_id = company.get("id")
        if company_id in hs_matched_ids:
            continue
        props    = company.get("properties", {})
        hs_name  = props.get("name") or ""
        if hs_name in cf_disregarded_hs:
            continue
        owner_id   = str(props.get("hubspot_owner_id") or "")
        owner_name = owner_map.get(owner_id, "Unassigned")
        deal       = company_deal_map.get(company_id)
        if deal:
            stage_id   = deal.get("properties", {}).get("dealstage", "")
            stage_name = CS_STAGE_NAMES.get(stage_id, stage_id)
            renewal_date = deal.get("properties", {}).get("cr_next_contract_start2")
        else:
            stage_name   = "No Active Deal"
            renewal_date = None
        accounts.append({
            "id":            company_id,
            "name":          hs_name or "Unknown",
            "owner_name":    owner_name,
            "owner_id":      owner_id,
            "industry":      props.get("in_industry_dropdown") or "—",
            "deal_stage":    stage_name,
            "renewal_date":  renewal_date or "—",
            "possibly_stale": deal is None,
            # Minimal defaults so app.py doesn't KeyError
            "risk_score": 0, "risk_label": "Not Set", "risk_reasons": "—",
            "tier": "—", "days_to_renewal": None, "arr": None,
            "email_count_90d": 0, "outbound_count_90d": 0, "inbound_count_90d": 0,
            "days_since_any_email": None, "days_since_outbound_email": None,
            "meeting_count_90d": 0, "days_since_meeting": None,
            "days_since_last_contact": None, "total_touchpoints_90d": 0,
            "median_response_minutes": None, "response_times": [],
            "pendo_days_since_login": None, "pendo_visitor_ts": [],
            "campfire_arr": None, "campfire_account_status": None,
            "campfire_open_invoice_count": 0, "campfire_open_amount": None,
            "campfire_days_open": None, "campfire_has_overdue": False,
            "campfire_overdue_invoice_count": 0, "campfire_overdue_amount": None,
            "campfire_days_overdue": None, "campfire_contract_end": None,
            "campfire_contract_start": None,
            "chi_score": 0, "chi_color": "gray", "chi_label": "—", "chi_breakdown": {},
            "news_articles": [], "news_count": 0,
            # Source flag
            "campfire_source": False,
            "campfire_only":   True,
            "campfire_account_status": None,
        })

    return accounts
