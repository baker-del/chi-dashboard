import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_PREFS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "preferences.json")
_PREFS_DEFAULTS = {"response_threshold_hrs": 1.0, "no_contact_days": 30, "pendo_window": 90}

def _load_prefs():
    try:
        with open(_PREFS_PATH) as _f:
            return {**_PREFS_DEFAULTS, **json.load(_f)}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _PREFS_DEFAULTS.copy()

def _save_prefs(prefs):
    os.makedirs(os.path.dirname(_PREFS_PATH), exist_ok=True)
    with open(_PREFS_PATH, "w") as _f:
        json.dump(prefs, _f, indent=2)

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from difflib import SequenceMatcher
from backend.services.data_service import get_dashboard_data, build_owner_map
from backend.auth import get_user_access, allowed_csm_names
from backend.cache import load_cache, save_cache, is_cache_stale, refresh_in_background, cache_last_updated
from backend.connectors.campfire import load_mappings, save_mappings, get_campfire_arr, get_all_contracts_for_retention, _normalize as campfire_normalize
from backend.connectors.news import refresh_news_for_accounts
from backend.connectors.pendo import get_pendo_page_analytics, clear_page_analytics_cache
from backend.connectors.hubspot import get_all_customers_full, get_all_hs_companies_for_mapping

st.set_page_config(
    page_title="CHI Dashboard — ClearlyRated",
    page_icon="📊",
    layout="wide",
)

RISK_COLORS = {
    0: "#9e9e9e",  # Not set — gray
    1: "#4caf50",  # Very low — green
    2: "#8bc34a",  # Somewhat low — light green
    3: "#ff9800",  # Neutral — orange
    4: "#f44336",  # Somewhat high — red
    5: "#b71c1c",  # Very high — dark red
    6: "#9e9e9e",  # Churned — gray
}

RISK_EMOJI = {
    0: "⚪", 1: "🟢", 2: "🟢", 3: "🟡", 4: "🔴", 5: "🔴", 6: "⚫"
}

CHI_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


# ── Auth gate ──────────────────────────────────────────────────────────────────
if not st.user.is_logged_in:
    st.title("📊 ClearlyRated — CHI Dashboard")
    st.write("Sign in with your ClearlyRated Google account to continue.")
    st.login("google")
    st.stop()

_access = get_user_access(st.user.email)
if _access is None:
    st.error(f"Access denied. {st.user.email} is not authorized for this dashboard.")
    st.write("Contact Eric Gregg to request access.")
    st.button("Sign out", on_click=st.logout)
    st.stop()

_allowed_csms = allowed_csm_names(_access)  # None = all, list = restricted


@st.cache_data(ttl=3600, show_spinner=False)
def load_campfire_stats():
    return get_campfire_arr()


@st.cache_data(ttl=3600, show_spinner=False)
def load_retention_contracts():
    return get_all_contracts_for_retention()


@st.cache_data(ttl=3600, show_spinner=False)
def load_all_hs_companies():
    """All HubSpot companies (any lifecycle) for mapping dropdowns."""
    companies = get_all_hs_companies_for_mapping()
    return sorted(set(c["name"] for c in companies if c["name"]))


@st.cache_data(ttl=3600, show_spinner=False)
def load_team_meetings(days, owner_ids_key):
    """Team-level meeting summary — historical period + upcoming 90 days. owner_ids_key is a sorted tuple."""
    from backend.connectors.hubspot_activity import get_team_meetings_summary
    return get_team_meetings_summary(days=days, owner_ids=list(owner_ids_key) if owner_ids_key else None)


@st.cache_data(ttl=3600, show_spinner="Loading dashboard data...")
def load_accounts():
    cached = load_cache()
    if cached is not None:
        # Serve from disk — trigger a background refresh if stale
        if is_cache_stale():
            refresh_in_background(get_dashboard_data)
        return cached
    # No usable cache — fetch fresh and save
    accounts = get_dashboard_data()
    save_cache(accounts)
    return accounts


def renewal_urgency(days):
    if days is None:
        return "—"
    if days < 0:
        return f"⚠️ {abs(days)}d overdue"
    if days <= 30:
        return f"🔴 {days}d"
    if days <= 60:
        return f"🟡 {days}d"
    if days <= 90:
        return f"🟠 {days}d"
    return f"🟢 {days}d"


def contact_recency(days):
    """Color-code days since last contact."""
    if days is None:
        return "⚫ Never"
    if days <= 14:
        return f"🟢 {days}d"
    if days <= 30:
        return f"🟡 {days}d"
    if days <= 60:
        return f"🟠 {days}d"
    return f"🔴 {days}d"


# ── Header ─────────────────────────────────────────────────────────────────────
st.title("📊 ClearlyRated — CHI Dashboard")
_last_updated = cache_last_updated()
_stale_notice = " · ⏳ Background refresh in progress" if is_cache_stale() else ""
st.caption(f"Data updated: {_last_updated or 'loading now'} · Source: HubSpot + Pendo{_stale_notice}")

if st.button("🔄 Refresh Data Now"):
    st.cache_data.clear()
    from backend.cache import CACHE_PATH
    import os
    try:
        os.remove(CACHE_PATH)
    except FileNotFoundError:
        pass
    st.rerun()

if _access["role"] == "all":
    _page = st.sidebar.radio("View", ["📊 Dashboard", "🎯 Priorities", "💰 Revenue Analytics", "🔄 Retention Analytics", "📱 Page Analytics", "🔍 Data Health", "🗂️ Data Mapping"], label_visibility="collapsed")
else:
    _page = st.sidebar.radio("View", ["📊 Dashboard", "🎯 Priorities"], label_visibility="collapsed")

# ── Data Mapping page (admin only — rendered here, then st.stop()) ─────────────
if _page == "🗂️ Data Mapping":

    def _sim(a, b):
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    st.subheader("🗂️ Campfire ↔ HubSpot Data Mapping")
    st.caption("Check the rows you want to save, then click **Done — Apply to Dashboard**.")

    # ── Match rate summary ─────────────────────────────────────────────────────
    with st.expander("📊 Match Rate Stats", expanded=False):
        _cf_data_raw  = load_campfire_stats()   # by_name dict (from cache)
        _total_hs     = len(load_accounts())
        _total_cf     = len(_cf_data_raw)
        # Count how many HubSpot accounts have a Campfire ARR value
        _matched_count = sum(1 for a in load_accounts() if a.get("campfire_arr"))
        _pct = round(_matched_count / _total_hs * 100) if _total_hs else 0
        _m1, _m2, _m3 = st.columns(3)
        _m1.metric("HubSpot Accounts", _total_hs)
        _m2.metric("Campfire Clients", _total_cf)
        _m3.metric("✅ Matched", f"{_matched_count} ({_pct}%)")

    # Load current state
    _m = load_mappings()
    _mappings     = dict(_m["mappings"])
    _dis_hs       = list(_m["disregarded_hubspot"])
    _dis_cf       = list(_m["disregarded_campfire"])

    _cf_stats     = load_campfire_stats()
    _accounts_raw = load_accounts()

    # Build unmatched lists
    _mapped_hs      = set(_mappings.keys())
    _matched_cf     = set(_mappings.values())
    _hs_norm_set    = {campfire_normalize(a["name"]): a["name"] for a in _accounts_raw if a.get("name")}

    _unmatched_hs = sorted(
        [{"name": a["name"], "csm": a["owner_name"]}
         for a in _accounts_raw
         if a.get("name")
         and a["name"] not in _mapped_hs
         and a["name"] not in _dis_hs
         and not any(campfire_normalize(d["campfire_name"]) == campfire_normalize(a["name"])
                     for d in _cf_stats.values())],
        key=lambda x: x["name"]
    )

    _unmatched_cf = sorted(
        [data for key, data in _cf_stats.items()
         if data["campfire_name"] not in _matched_cf
         and data["campfire_name"] not in _dis_cf
         and key not in _hs_norm_set],
        key=lambda x: x["campfire_name"]
    )

    NO_MATCH = "🚫 No Match"

    def _save_fast():
        """Save YAML only — used by Remove/Restore buttons in expanders."""
        save_mappings(_mappings, _dis_hs, _dis_cf)
        st.rerun()

    def _apply_checked_and_save():
        """Read all checked rows from session state, apply, save, clear cache, rerun."""
        _cur_mode = st.session_state.get("mapping_mode", "Campfire → HubSpot")
        if _cur_mode == "Campfire → HubSpot":
            for _i, _cf in enumerate(_unmatched_cf):
                if st.session_state.get(f"chk_cf_{_i}"):
                    _sel = st.session_state.get(f"cf_{_i}", NO_MATCH)
                    _sel = _sel[2:] if _sel.startswith("✅ ") else _sel  # strip indicator
                    if _sel == NO_MATCH:
                        if _cf["campfire_name"] not in _dis_cf:
                            _dis_cf.append(_cf["campfire_name"])
                    else:
                        _mappings[_sel] = _cf["campfire_name"]
        else:
            for _i, _hs in enumerate(_unmatched_hs):
                if st.session_state.get(f"chk_hs_{_i}"):
                    _sel = st.session_state.get(f"hs_{_i}", NO_MATCH)
                    if _sel == NO_MATCH:
                        if _hs["name"] not in _dis_hs:
                            _dis_hs.append(_hs["name"])
                    else:
                        _mappings[_hs["name"]] = _sel
        save_mappings(_mappings, _dis_hs, _dis_cf)
        st.cache_data.clear()
        from backend.cache import CACHE_PATH
        try:
            os.remove(CACHE_PATH)
        except FileNotFoundError:
            pass
        # Clear all checkbox state so nothing is pre-checked on the fresh list
        for _k in list(st.session_state.keys()):
            if _k.startswith("chk_cf_") or _k.startswith("chk_hs_"):
                del st.session_state[_k]
        st.rerun()

    # ── Top controls: Done + Select All / Deselect All ─────────────────────────
    _btn1, _btn2, _btn3, _spacer = st.columns([2, 1, 1, 4])

    if _btn1.button("✅ Done — Apply to Dashboard", type="primary"):
        _apply_checked_and_save()

    if _btn2.button("☑ Select All"):
        _cur_mode = st.session_state.get("mapping_mode", "Campfire → HubSpot")
        if _cur_mode == "Campfire → HubSpot":
            for _i in range(len(_unmatched_cf)):
                st.session_state[f"chk_cf_{_i}"] = True
        else:
            for _i in range(len(_unmatched_hs)):
                st.session_state[f"chk_hs_{_i}"] = True
        st.rerun()

    if _btn3.button("☐ Deselect All"):
        _cur_mode = st.session_state.get("mapping_mode", "Campfire → HubSpot")
        if _cur_mode == "Campfire → HubSpot":
            for _i in range(len(_unmatched_cf)):
                st.session_state[f"chk_cf_{_i}"] = False
        else:
            for _i in range(len(_unmatched_hs)):
                st.session_state[f"chk_hs_{_i}"] = False
        st.rerun()

    st.divider()

    # ── Mode toggle ────────────────────────────────────────────────────────────
    _mode = st.radio("Primary list", ["Campfire → HubSpot", "HubSpot → Campfire"],
                     horizontal=True, label_visibility="collapsed", key="mapping_mode")

    st.divider()

    # ── Full mapping list ──────────────────────────────────────────────────────
    if _mode == "Campfire → HubSpot":
        st.markdown(f"**Unmatched Campfire clients — {len(_unmatched_cf)} remaining**")
        st.caption("Pick the correct HubSpot match (or 'No Match'), check the box, then click Done.")

        # Full HubSpot name list — all companies regardless of lifecycle stage
        _all_hs_names       = load_all_hs_companies()
        _hs_already_mapped  = set(_mappings.keys())
        # Split into unmatched (no indicator) and already-mapped (✅ prefix)
        _hs_unmatched_names = sorted([n for n in _all_hs_names if n not in _hs_already_mapped])
        _hs_already_mapped_display = sorted([f"✅ {n}" for n in _hs_already_mapped if n in set(_all_hs_names)])
        _hs_all_options     = _hs_unmatched_names + _hs_already_mapped_display

        _h1, _h2, _h3, _h4 = st.columns([3, 1, 3, 1])
        _h1.markdown("**Campfire Client**")
        _h2.markdown("**ARR**")
        _h3.markdown("**→ HubSpot Account** (✅ = already mapped to another Campfire client)")
        _h4.markdown("**Save?**")

        for _i, _cf in enumerate(_unmatched_cf):
            _cf_name = _cf["campfire_name"]
            _opts = [NO_MATCH] + _hs_all_options
            _best = max(_hs_unmatched_names, key=lambda n: _sim(_cf_name, n), default=None)
            _best_idx = _opts.index(_best) if _best and _best in _opts else 0

            _r1, _r2, _r3, _r4 = st.columns([3, 1, 3, 1])
            _r1.write(_cf_name)
            _r2.write(f"${_cf['arr']:,.0f}")
            _r3.selectbox("", _opts, index=_best_idx,
                          key=f"cf_{_i}", label_visibility="collapsed")
            _r4.checkbox("", key=f"chk_cf_{_i}", label_visibility="collapsed")

    else:  # HubSpot → Campfire
        st.markdown(f"**Unmatched HubSpot accounts — {len(_unmatched_hs)} remaining**")
        st.caption("Pick the correct Campfire match (or 'No Match'), check the box, then click Done.")

        _cf_options_alpha = sorted([d["campfire_name"] for d in _unmatched_cf])

        _h1, _h2, _h3, _h4 = st.columns([3, 1, 3, 1])
        _h1.markdown("**HubSpot Account**")
        _h2.markdown("**CSM**")
        _h3.markdown("**→ Campfire Client**")
        _h4.markdown("**Save?**")

        for _i, _hs in enumerate(_unmatched_hs):
            _hs_name = _hs["name"]
            _opts = [NO_MATCH] + _cf_options_alpha
            _best = max(_cf_options_alpha, key=lambda n: _sim(_hs_name, n), default=None)
            _best_idx = _opts.index(_best) if _best and _best in _opts else 0

            _r1, _r2, _r3, _r4 = st.columns([3, 1, 3, 1])
            _r1.write(_hs_name)
            _r2.write(_hs["csm"])
            _r3.selectbox("", _opts, index=_best_idx,
                          key=f"hs_{_i}", label_visibility="collapsed")
            _r4.checkbox("", key=f"chk_hs_{_i}", label_visibility="collapsed")

    st.divider()

    # ── Current manual mappings ────────────────────────────────────────────────
    with st.expander(f"✅ Current manual mappings ({len(_mappings)})", expanded=False):
        if _mappings:
            _mc_h1, _mc_h2, _mc_h3, _mc_h4 = st.columns([3, 3, 1, 1])
            _mc_h1.markdown("**HubSpot Account**")
            _mc_h2.markdown("**Campfire Client(s)**")
            _mc_h3.markdown("**Add**")
            _mc_h4.markdown("**Remove**")
            _cf_all_names = sorted(d["campfire_name"] for d in _cf_stats.values())
            for _hs_n, _cf_val in list(_mappings.items()):
                _cf_display = " + ".join(_cf_val) if isinstance(_cf_val, list) else _cf_val
                _mc1, _mc2, _mc3, _mc4 = st.columns([3, 3, 1, 1])
                _mc1.write(_hs_n)
                _mc2.write(_cf_display)
                # Add another Campfire client to this mapping
                _add_opts = [n for n in _cf_all_names if n not in (_cf_val if isinstance(_cf_val, list) else [_cf_val])]
                _add_sel = _mc3.selectbox("", ["—"] + _add_opts, key=f"add_{_hs_n}", label_visibility="collapsed")
                if _add_sel != "—":
                    existing = _cf_val if isinstance(_cf_val, list) else [_cf_val]
                    _mappings[_hs_n] = existing + [_add_sel]
                    _save_fast()
                if _mc4.button("Remove", key=f"rm_{_hs_n}"):
                    del _mappings[_hs_n]
                    _save_fast()
        else:
            st.info("No manual mappings yet.")

    with st.expander(f"🚫 Disregarded accounts ({len(_dis_hs) + len(_dis_cf)})", expanded=False):
        if _dis_hs:
            st.markdown("**HubSpot accounts marked 'No Match':**")
            for _n in list(_dis_hs):
                _dc1, _dc2 = st.columns([5, 1])
                _dc1.write(_n)
                if _dc2.button("Restore", key=f"rhs_{_n}"):
                    _dis_hs.remove(_n)
                    _save_fast()
        if _dis_cf:
            st.markdown("**Campfire clients marked 'No Match':**")
            for _n in list(_dis_cf):
                _dc1, _dc2 = st.columns([5, 1])
                _dc1.write(_n)
                if _dc2.button("Restore", key=f"rcf_{_n}"):
                    _dis_cf.remove(_n)
                    _save_fast()
        if not _dis_hs and not _dis_cf:
            st.info("Nothing disregarded yet.")

    st.stop()  # Don't render the dashboard when on this page

# ── Page Analytics page ────────────────────────────────────────────────────────
if _page == "📱 Page Analytics":
    st.subheader("📱 Page Analytics")
    st.caption("Pendo usage data for the Overview and Legacy pages. Cached 12 hours — use Refresh to pull fresh data.")

    _pa_col1, _pa_col2 = st.columns([2, 6])
    _pa_window = _pa_col1.selectbox("Time Window", ["3 Months", "6 Months", "9 Months", "12 Months"], index=0)
    _pa_days = {"3 Months": 90, "6 Months": 180, "9 Months": 270, "12 Months": 365}[_pa_window]

    if st.button("🔄 Refresh Page Analytics"):
        clear_page_analytics_cache()
        st.cache_data.clear()
        st.rerun()

    with st.spinner("Loading page analytics from Pendo..."):
        _pa_raw = get_pendo_page_analytics(days=_pa_days)

    # Pull account list for name/CSM mapping
    _pa_accounts = load_accounts()
    _pa_df_base  = pd.DataFrame(_pa_accounts)

    from backend.connectors.pendo import _normalize as pendo_norm

    # Build lookup: norm_name → {name, owner_name, arr}
    _pa_acct_map = {
        pendo_norm(a["name"]): a
        for a in _pa_accounts if a.get("name")
    }

    # ── Helper: engagement tier from median mins/visit ─────────────────────────
    def _eng_tier(median_mins):
        if median_mins <= 1:   return "🔴 Low (≤1 min)"
        if median_mins <= 3:   return "🟡 Med (1–3 min)"
        return                        "🟢 High (3+ min)"

    # ── Aggregate summary stats ────────────────────────────────────────────────
    _total_ov_views   = sum(v["overview"]["total_views"]          for v in _pa_raw.values())
    _total_ov_users   = sum(v["overview"]["unique_visitors"]      for v in _pa_raw.values())
    _total_ov_visits  = sum(v["overview"]["total_visits"]         for v in _pa_raw.values())
    _total_ov_bounces = sum(v["overview"]["bounce_visits"]        for v in _pa_raw.values())
    _total_ov_mins    = sum(v["overview"]["total_minutes"]        for v in _pa_raw.values())
    _total_leg_views  = sum(v["legacy_total"]["total_views"]      for v in _pa_raw.values())
    _total_leg_users  = sum(v["legacy_total"]["unique_visitors"]  for v in _pa_raw.values())
    _total_leg_visits = sum(v["legacy_total"]["total_visits"]     for v in _pa_raw.values())
    _total_leg_bounces= sum(v["legacy_total"]["bounce_visits"]    for v in _pa_raw.values())
    _total_leg_mins   = sum(v["legacy_total"]["total_minutes"]    for v in _pa_raw.values())
    _accts_with_ov    = sum(1 for v in _pa_raw.values() if v["overview"]["total_views"] > 0)
    _accts_with_leg   = sum(1 for v in _pa_raw.values() if v["legacy_total"]["total_views"] > 0)

    _ov_bounce_pct  = round(_total_ov_bounces  / _total_ov_visits  * 100) if _total_ov_visits  else 0
    _leg_bounce_pct = round(_total_leg_bounces / _total_leg_visits * 100) if _total_leg_visits else 0
    _ov_avg_mins    = round(_total_ov_mins  / _total_ov_visits,  1) if _total_ov_visits  else 0
    _leg_avg_mins   = round(_total_leg_mins / _total_leg_visits, 1) if _total_leg_visits else 0
    _ov_views_per_user  = round(_total_ov_views  / _total_ov_users,  1) if _total_ov_users  else 0
    _leg_views_per_user = round(_total_leg_views / _total_leg_users, 1) if _total_leg_users else 0

    # ── Side-by-side comparison ────────────────────────────────────────────────
    st.markdown("#### Engagement Quality — Overview vs Legacy")
    st.caption("Bounce = visit ≤ 1 min · Visits = unique visitor×day combinations")

    _cmp_data = {
        "Metric": [
            "Total Views", "Unique Users", "Total Visits",
            "Bounce Rate", "Avg Mins / Visit", "Views per User", "Accounts w/ Usage",
        ],
        "Overview": [
            f"{_total_ov_views:,}", f"{_total_ov_users:,}", f"{_total_ov_visits:,}",
            f"{_ov_bounce_pct}% ⚠️" if _ov_bounce_pct > 60 else f"{_ov_bounce_pct}%",
            f"{_ov_avg_mins} min {_eng_tier(_ov_avg_mins)}",
            str(_ov_views_per_user), str(_accts_with_ov),
        ],
        "Legacy Pages": [
            f"{_total_leg_views:,}", f"{_total_leg_users:,}", f"{_total_leg_visits:,}",
            f"{_leg_bounce_pct}% ⚠️" if _leg_bounce_pct > 60 else f"{_leg_bounce_pct}%",
            f"{_leg_avg_mins} min {_eng_tier(_leg_avg_mins)}",
            str(_leg_views_per_user), str(_accts_with_leg),
        ],
    }
    st.dataframe(pd.DataFrame(_cmp_data), use_container_width=False, hide_index=True)

    st.divider()

    # ── Per-account table ──────────────────────────────────────────────────────
    st.markdown(f"#### By Account — last {_pa_window}")

    _pa_rows = []
    for norm_key, pdata in _pa_raw.items():
        acct = _pa_acct_map.get(norm_key)
        name     = acct["name"]      if acct else norm_key
        csm      = acct["owner_name"] if acct else "—"
        arr      = acct.get("campfire_arr") if acct else None

        ov  = pdata["overview"]
        leg = pdata["legacy_total"]
        ll  = pdata["legacy_list"]
        ld  = pdata["legacy_detail"]

        _pa_rows.append({
            "Account":           name,
            "CSM":               csm,
            "ARR":               arr,
            # Overview
            "OV Visits":         ov["total_visits"],
            "OV Users":          ov["unique_visitors"],
            "OV Bounce %":       ov["bounce_pct"],
            "OV Median Mins":    ov["median_mins_per_visit"],
            "OV Engagement":     _eng_tier(ov["median_mins_per_visit"]),
            "OV Views/User":     round(ov["total_views"] / ov["unique_visitors"], 1) if ov["unique_visitors"] else 0,
            # Legacy combined
            "Leg Visits":        leg["total_visits"],
            "Leg Users":         leg["unique_visitors"],
            "Leg Bounce %":      leg["bounce_pct"],
            "Leg Median Mins":   leg["median_mins_per_visit"],
            "Leg Engagement":    _eng_tier(leg["median_mins_per_visit"]),
            "Leg Views/User":    round(leg["total_views"] / leg["unique_visitors"], 1) if leg["unique_visitors"] else 0,
            # Legacy breakdown
            "Leg List Views":    ll["total_views"],
            "Leg Detail Views":  ld["total_views"],
            # Raw for filtering
            "_ov_views":         ov["total_views"],
            "_leg_views":        leg["total_views"],
        })

    _pa_table = pd.DataFrame(_pa_rows)

    # Filters
    _paf1, _paf2 = st.columns([2, 6])
    _pa_csm_filter   = _paf1.selectbox("Filter by CSM", ["All CSMs"] + sorted(_pa_table["CSM"].dropna().unique().tolist()))
    _pa_usage_filter = _paf2.radio("Show", ["All accounts", "Using Overview", "Using Legacy", "Using both", "Using neither"], horizontal=True)

    if _pa_csm_filter != "All CSMs":
        _pa_table = _pa_table[_pa_table["CSM"] == _pa_csm_filter]
    if _pa_usage_filter == "Using Overview":
        _pa_table = _pa_table[_pa_table["_ov_views"] > 0]
    elif _pa_usage_filter == "Using Legacy":
        _pa_table = _pa_table[_pa_table["_leg_views"] > 0]
    elif _pa_usage_filter == "Using both":
        _pa_table = _pa_table[(_pa_table["_ov_views"] > 0) & (_pa_table["_leg_views"] > 0)]
    elif _pa_usage_filter == "Using neither":
        _pa_table = _pa_table[(_pa_table["_ov_views"] == 0) & (_pa_table["_leg_views"] == 0)]

    _pa_table = _pa_table.sort_values("OV Visits", ascending=False)
    _pa_table["ARR"] = _pa_table["ARR"].map(lambda x: f"${x:,.0f}" if pd.notna(x) and x else "—")
    _pa_table["OV Bounce %"]  = _pa_table["OV Bounce %"].map(lambda x: f"{x}%")
    _pa_table["Leg Bounce %"] = _pa_table["Leg Bounce %"].map(lambda x: f"{x}%")

    st.dataframe(
        _pa_table[["Account", "CSM", "ARR",
                   "OV Visits", "OV Users", "OV Median Mins", "OV Bounce %", "OV Engagement", "OV Views/User",
                   "Leg Visits", "Leg Users", "Leg Median Mins", "Leg Bounce %", "Leg Engagement", "Leg Views/User",
                   "Leg List Views", "Leg Detail Views"]],
        use_container_width=True, hide_index=True, height=600,
    )

    st.caption("OV = Overview page · Legacy = /surveys list + individual survey pages combined")
    st.stop()

# ── Data Health page ───────────────────────────────────────────────────────────
if _page == "🔍 Data Health":
    st.subheader("🔍 Data Health — HubSpot ↔ Campfire Alignment")
    st.caption("Cross-reference of all HubSpot customers vs Campfire contracts. Identifies gaps, missing CSMs, and suspect records.")

    with st.spinner("Loading HubSpot + Campfire data..."):
        from backend.connectors.campfire import get_campfire_data, _normalize as cf_norm
        from backend.connectors.hubspot import get_owners
        _dh_owners    = get_owners()
        _dh_owner_map = build_owner_map(_dh_owners)
        _dh_all_hs    = get_all_customers_full()          # all 652 HS customers
        _dh_cf        = get_campfire_data()
        _dh_cf_by_name = _dh_cf["by_name"]
        _dh_cf_by_deal = _dh_cf["by_deal_id"]
        _dh_mappings   = load_mappings()
        _dh_manual     = _dh_mappings.get("mappings", {})
        _dh_dis_cf     = set(_dh_mappings.get("disregarded_campfire", []))
        _dh_dis_hs     = set(_dh_mappings.get("disregarded_hubspot", []))

    # Build mapped Campfire names set (supports list values)
    _dh_mapped_cf_names: set = set()
    for v in _dh_manual.values():
        if isinstance(v, list): _dh_mapped_cf_names.update(v)
        else: _dh_mapped_cf_names.add(v)

    # Build norm→HS lookup
    _dh_hs_norm = {cf_norm(c["name"]): c for c in _dh_all_hs if c["name"]}

    # Classify each HubSpot customer
    _dh_no_csm, _dh_no_cf, _dh_matched = [], [], []
    for c in _dh_all_hs:
        name     = c["name"]
        owner_id = c["owner_id"]
        csm_name = _dh_owner_map.get(str(owner_id), "") if owner_id else ""
        norm     = cf_norm(name)

        in_cf_name   = norm in _dh_cf_by_name
        in_cf_manual = name in _dh_manual
        in_cf_dis    = name in _dh_dis_hs
        has_cf = in_cf_name or in_cf_manual

        cf_arr = None
        cf_name_display = ""
        if in_cf_manual:
            mapped = _dh_manual[name]
            names_list = mapped if isinstance(mapped, list) else [mapped]
            cf_name_display = " + ".join(names_list)
            cf_arr = sum((_dh_cf_by_name.get(cf_norm(n), {}).get("arr") or 0) for n in names_list)
        elif in_cf_name:
            rec = _dh_cf_by_name[norm]
            cf_name_display = rec.get("campfire_name", "")
            cf_arr = rec.get("arr")

        row = {
            "HubSpot ID":    c["id"],
            "Account":       name,
            "CSM":           csm_name or "—",
            "owner_id":      owner_id,
            "has_cf":        has_cf,
            "cf_name":       cf_name_display,
            "cf_arr":        cf_arr,
            "norm":          norm,
        }

        if not owner_id:
            _dh_no_csm.append(row)
        if not has_cf and not in_cf_dis:
            _dh_no_cf.append(row)
        if has_cf:
            _dh_matched.append(row)

    # Campfire active clients unmatched to any HubSpot customer
    _dh_unmatched_cf = []
    for norm_key, data in _dh_cf_by_name.items():
        cf_name = data["campfire_name"]
        if cf_name in _dh_dis_cf or (data.get("arr") or 0) <= 0: continue
        if norm_key in _dh_hs_norm or cf_name in _dh_mapped_cf_names: continue
        # Find closest HubSpot name suggestion
        from difflib import SequenceMatcher
        best_hs = max(_dh_hs_norm.keys(), key=lambda k: SequenceMatcher(None, norm_key, k).ratio(), default="")
        best_score = SequenceMatcher(None, norm_key, best_hs).ratio() if best_hs else 0
        suggestion = _dh_hs_norm[best_hs]["name"] if best_score > 0.5 else ""
        _dh_unmatched_cf.append({
            "Campfire Client": cf_name,
            "ARR": data.get("arr") or 0,
            "Why not matching": f'norm="{norm_key}" — no HubSpot name normalizes to this',
            "Closest HubSpot match": suggestion or "—",
            "Match confidence": f"{round(best_score*100)}%" if suggestion else "—",
        })

    # ── Summary tiles ──────────────────────────────────────────────────────────
    _t1, _t2, _t3, _t4, _t5 = st.columns(5)
    _t1.metric("✅ Matched",            len(_dh_matched))
    _t2.metric("⚠️ No CSM Assigned",    len(_dh_no_csm))
    _t3.metric("❌ No Campfire Match",  len(_dh_no_cf))
    _t4.metric("🔶 Campfire Unmatched", len(_dh_unmatched_cf))
    _t5.metric("📋 Total HS Customers", len(_dh_all_hs))

    st.divider()

    # ── Section 1: No CSM assigned ─────────────────────────────────────────────
    with st.expander(f"⚠️ HubSpot customers with no CSM assigned ({len(_dh_no_csm)})", expanded=True):
        st.caption("These are marked 'customer' in HubSpot but have no owner. Assign a CSM or review if still active.")
        if _dh_no_csm:
            _no_csm_df = pd.DataFrame(_dh_no_csm)[["HubSpot ID", "Account", "cf_name"]].rename(columns={"cf_name": "Campfire Match"})
            _no_csm_df["HubSpot Link"] = _no_csm_df["HubSpot ID"].map(
                lambda x: f"https://app.hubspot.com/contacts/242622/company/{x}"
            )
            st.dataframe(_no_csm_df, use_container_width=True, hide_index=True)
        else:
            st.success("All customers have a CSM assigned.")

    # ── Section 2: Campfire unmatched ──────────────────────────────────────────
    with st.expander(f"🔶 Campfire active clients with no HubSpot match ({len(_dh_unmatched_cf)})", expanded=True):
        st.caption("These have active ARR in Campfire but no corresponding HubSpot customer record.")
        if _dh_unmatched_cf:
            _ucf_df = pd.DataFrame(_dh_unmatched_cf)
            _ucf_df["ARR"] = _ucf_df["ARR"].map(lambda x: f"${x:,.0f}")
            st.dataframe(_ucf_df, use_container_width=True, hide_index=True)
        else:
            st.success("All active Campfire clients are matched.")

    # ── Section 3: HubSpot customers without Campfire ─────────────────────────
    st.divider()
    st.markdown(f"#### ❌ HubSpot customers with no Campfire subscription ({len(_dh_no_cf)})")
    st.caption("These are marked 'customer' in HubSpot but have no active Campfire contract. "
               "**Recommendation:** verify each — if they're churned or inactive, update HubSpot lifecycle to 'Other' or create a Campfire mapping.")

    # Add closest Campfire suggestion for each
    from difflib import SequenceMatcher as _SM
    _dh_cf_names_list = list(_dh_cf_by_name.keys())

    _no_cf_rows = []
    for r in sorted(_dh_no_cf, key=lambda x: x["Account"]):
        norm = r["norm"]
        best_k = max(_dh_cf_names_list, key=lambda k: _SM(None, norm, k).ratio(), default="") if _dh_cf_names_list else ""
        score  = _SM(None, norm, best_k).ratio() if best_k else 0
        suggestion = _dh_cf_by_name[best_k]["campfire_name"] if score > 0.55 else ""
        _no_cf_rows.append({
            "Account":           r["Account"],
            "CSM":               r["CSM"],
            "HubSpot ID":        r["HubSpot ID"],
            "Closest Campfire":  suggestion or "— no close match",
            "Confidence":        f"{round(score*100)}%" if suggestion else "—",
            "Recommendation":    "Map to Campfire" if suggestion else "Update HS lifecycle to former customer",
        })

    _no_cf_df = pd.DataFrame(_no_cf_rows)

    # Filter controls
    _dh_f1, _dh_f2 = st.columns([2, 4])
    _dh_rec_filter = _dh_f1.selectbox("Filter by recommendation", ["All", "Map to Campfire", "Update HS lifecycle to former customer"])
    _dh_csm_filter = _dh_f2.selectbox("Filter by CSM", ["All"] + sorted(_no_cf_df["CSM"].dropna().unique().tolist()))

    if _dh_rec_filter != "All":
        _no_cf_df = _no_cf_df[_no_cf_df["Recommendation"] == _dh_rec_filter]
    if _dh_csm_filter != "All":
        _no_cf_df = _no_cf_df[_no_cf_df["CSM"] == _dh_csm_filter]

    st.caption(f"Showing {len(_no_cf_df)} accounts · 'Map to Campfire' = likely match exists · 'Update HS lifecycle' = no Campfire record found")
    st.dataframe(_no_cf_df[["Account", "CSM", "Closest Campfire", "Confidence", "Recommendation", "HubSpot ID"]],
                 use_container_width=True, hide_index=True, height=500)

    st.stop()

# ── Revenue Analytics page ─────────────────────────────────────────────────────
if _page == "💰 Revenue Analytics":
    _ra_accounts = load_accounts()
    _ra_df = pd.DataFrame(_ra_accounts)

    st.subheader("💰 Revenue Analytics")
    st.caption("ARR at risk, renewal pipeline, and revenue health — based on HubSpot + Campfire data.")

    # ── ARR source: prefer Campfire ARR, fall back to HubSpot ARR ─────────────
    _ra_df["_arr"] = _ra_df["campfire_arr"].fillna(0)  # Campfire is source of truth; unknown = 0

    # ── Top-level revenue metrics ──────────────────────────────────────────────
    _total_arr        = _ra_df["_arr"].sum()
    _at_risk_arr      = _ra_df.loc[_ra_df["chi_color"] == "red", "_arr"].sum()
    _high_risk_arr    = _ra_df.loc[_ra_df["risk_score"] >= 4, "_arr"].sum()
    _renewing_30_arr  = _ra_df.loc[
        _ra_df["days_to_renewal"].notna() & (_ra_df["days_to_renewal"] >= 0) & (_ra_df["days_to_renewal"] <= 30), "_arr"
    ].sum()
    _renewing_90_arr  = _ra_df.loc[
        _ra_df["days_to_renewal"].notna() & (_ra_df["days_to_renewal"] >= 0) & (_ra_df["days_to_renewal"] <= 90), "_arr"
    ].sum()
    _overdue_inv_arr  = _ra_df.loc[_ra_df["campfire_has_overdue"] == True, "campfire_overdue_amount"].sum()
    _no_pendo_arr     = _ra_df.loc[
        _ra_df["pendo_days_since_login"].isna() | (_ra_df["pendo_days_since_login"] > 90), "_arr"
    ].sum()

    _r1, _r2, _r3, _r4 = st.columns(4)
    _r1.metric("💵 Total ARR", f"${_total_arr:,.0f}")
    _r2.metric("🔴 CHI Critical ARR", f"${_at_risk_arr:,.0f}",
               delta=f"{round(_at_risk_arr/_total_arr*100,1)}% of total" if _total_arr else None,
               delta_color="inverse")
    _r3.metric("⚠️ High Risk ARR", f"${_high_risk_arr:,.0f}",
               delta=f"{round(_high_risk_arr/_total_arr*100,1)}% of total" if _total_arr else None,
               delta_color="inverse")
    _r4.metric("💳 Overdue Invoice $", f"${_overdue_inv_arr:,.0f}" if _overdue_inv_arr else "—")

    _r5, _r6, _r7, _r8 = st.columns(4)
    _r5.metric("🔔 ARR Renewing (30d)", f"${_renewing_30_arr:,.0f}")
    _r6.metric("🔔 ARR Renewing (90d)", f"${_renewing_90_arr:,.0f}")
    _r7.metric("🔒 No Login (90d) ARR", f"${_no_pendo_arr:,.0f}",
               delta=f"{round(_no_pendo_arr/_total_arr*100,1)}% of total" if _total_arr else None,
               delta_color="inverse")
    _n_campfire_arr  = int(_ra_df["campfire_arr"].notna().sum())
    _n_no_arr        = int(_ra_df["campfire_arr"].isna().sum())
    _r8.metric("📊 Matched ARR Accounts", f"{_n_campfire_arr} / {len(_ra_df)}")

    with st.expander("🔎 ARR Source Breakdown (use to verify totals)", expanded=False):
        st.caption(
            f"Total ARR = ${_total_arr:,.0f} (Campfire only) across {len(_ra_df)} accounts. "
            f"**{_n_campfire_arr}** have Campfire ARR · "
            f"**{_n_no_arr}** have no Campfire record (excluded from totals)."
        )
        _arr_src_rows = [
            {"Source": "✅ Has Campfire ARR", "Accounts": _n_campfire_arr,
             "Total ARR": f"${_ra_df.loc[_ra_df['campfire_arr'].notna(), 'campfire_arr'].sum():,.0f}"},
            {"Source": "❌ No Campfire match", "Accounts": _n_no_arr, "Total ARR": "—"},
        ]
        st.dataframe(pd.DataFrame(_arr_src_rows), hide_index=True, use_container_width=False)
        st.caption("Accounts with no Campfire match are visible in the dashboard but excluded from all ARR totals. Use Data Mapping to link them.")

    st.divider()

    # ── ARR by CSM ────────────────────────────────────────────────────────────
    st.markdown("#### ARR by CSM")
    _csm_arr = _ra_df.groupby("owner_name").agg(
        Accounts=("name", "count"),
        Total_ARR=("_arr", "sum"),
        CHI_Critical_ARR=("_arr", lambda x: x[_ra_df.loc[x.index, "chi_color"] == "red"].sum()),
        High_Risk_ARR=("_arr", lambda x: x[_ra_df.loc[x.index, "risk_score"] >= 4].sum()),
        Renewing_30d_ARR=("_arr", lambda x: x[
            _ra_df.loc[x.index, "days_to_renewal"].notna() &
            (_ra_df.loc[x.index, "days_to_renewal"] >= 0) &
            (_ra_df.loc[x.index, "days_to_renewal"] <= 30)
        ].sum()),
        Overdue_Invoice=("campfire_overdue_amount", "sum"),
    ).reset_index().rename(columns={"owner_name": "CSM"})

    _csm_arr = _csm_arr.sort_values("Total_ARR", ascending=False)

    # Total row (raw values, prepended before formatting)
    _csm_total_row = pd.DataFrame([{
        "CSM":             "📊 Total",
        "Accounts":        int(_csm_arr["Accounts"].sum()),
        "Total_ARR":       _csm_arr["Total_ARR"].sum(),
        "CHI_Critical_ARR":_csm_arr["CHI_Critical_ARR"].sum(),
        "High_Risk_ARR":   _csm_arr["High_Risk_ARR"].sum(),
        "Renewing_30d_ARR":_csm_arr["Renewing_30d_ARR"].sum(),
        "Overdue_Invoice": _csm_arr["Overdue_Invoice"].sum(),
    }])
    _csm_arr = pd.concat([_csm_total_row, _csm_arr], ignore_index=True)

    _csm_arr["% at Risk"] = (_csm_arr["CHI_Critical_ARR"] / _csm_arr["Total_ARR"] * 100).round(1).map(
        lambda x: f"{x}%" if pd.notna(x) and x > 0 else "—"
    )
    _csm_arr["Total ARR"]          = _csm_arr["Total_ARR"].map(lambda x: f"${x:,.0f}" if x else "—")
    _csm_arr["CHI Critical ARR"]   = _csm_arr["CHI_Critical_ARR"].map(lambda x: f"${x:,.0f}" if x else "—")
    _csm_arr["High Risk ARR"]      = _csm_arr["High_Risk_ARR"].map(lambda x: f"${x:,.0f}" if x else "—")
    _csm_arr["Renewing 30d ARR"]   = _csm_arr["Renewing_30d_ARR"].map(lambda x: f"${x:,.0f}" if x else "—")
    _csm_arr["Overdue Invoices $"] = _csm_arr["Overdue_Invoice"].map(lambda x: f"${x:,.0f}" if x else "—")

    st.dataframe(
        _csm_arr[["CSM", "Accounts", "Total ARR", "CHI Critical ARR", "% at Risk",
                  "High Risk ARR", "Renewing 30d ARR", "Overdue Invoices $"]],
        use_container_width=True, hide_index=True
    )

    st.divider()

    # ── Risk-stratified ARR ───────────────────────────────────────────────────
    st.markdown("#### ARR by Risk Band")
    _risk_bands = [{"Band": "📊 Total", "Accounts": len(_ra_df),
                    "ARR": f"${_total_arr:,.0f}", "% of Total ARR": "100%"}]
    for _label, _mask in [
        ("🟢 Low Risk (1-2)", _ra_df["risk_score"].isin([1, 2])),
        ("🟡 Medium Risk (3)", _ra_df["risk_score"] == 3),
        ("🔴 High Risk (4-5)", _ra_df["risk_score"] >= 4),
        ("⚪ Not Set", _ra_df["risk_score"] == 0),
    ]:
        _sub = _ra_df[_mask]
        _risk_bands.append({
            "Band": _label,
            "Accounts": len(_sub),
            "ARR": f"${_sub['_arr'].sum():,.0f}",
            "% of Total ARR": f"{round(_sub['_arr'].sum() / _total_arr * 100, 1)}%" if _total_arr else "—",
        })
    st.dataframe(pd.DataFrame(_risk_bands), use_container_width=False, hide_index=True)

    st.divider()

    # ── Renewal pipeline ──────────────────────────────────────────────────────
    st.markdown("#### Renewal Pipeline (Next 90 Days)")
    _renewing = _ra_df[
        _ra_df["days_to_renewal"].notna() &
        (_ra_df["days_to_renewal"] >= 0) &
        (_ra_df["days_to_renewal"] <= 90)
    ].copy()
    _renewing = _renewing.sort_values("days_to_renewal")

    if len(_renewing):
        _renewing["Renewal"] = _renewing["days_to_renewal"].map(renewal_urgency)
        _renewing["ARR"] = _renewing["_arr"].map(lambda x: f"${x:,.0f}" if pd.notna(x) and x else "—")
        _renewing["CHI"] = _renewing.apply(
            lambda r: f"{CHI_EMOJI.get(r['chi_color'], '⚪')} {r['chi_score']}", axis=1
        )
        _renewing["Risk"] = _renewing.apply(
            lambda r: f"{RISK_EMOJI.get(r['risk_score'], '⚪')} {r['risk_label']}", axis=1
        )
        _renewing_detail = _renewing[["name", "owner_name", "ARR", "Renewal", "CHI", "Risk", "deal_stage"]].rename(
            columns={"name": "Account", "owner_name": "CSM", "deal_stage": "Stage"}
        )
        _renewing_total = pd.DataFrame([{
            "Account": "📊 Total",
            "CSM": f"{len(_renewing)} accounts",
            "ARR": f"${_renewing['_arr'].sum():,.0f}",
            "Renewal": "", "CHI": "", "Risk": "", "Stage": "",
        }])
        st.dataframe(
            pd.concat([_renewing_total, _renewing_detail], ignore_index=True),
            use_container_width=True, hide_index=True
        )
    else:
        st.info("No accounts renewing in the next 90 days.")

    st.divider()

    # ── Churned / expired contracts ───────────────────────────────────────────
    st.markdown("#### ⚠️ Accounts Without Active Deals (Stale / Potential Churn)")
    _stale = _ra_df[_ra_df["possibly_stale"] == True].copy()
    if len(_stale):
        _stale["ARR"] = _stale["_arr"].map(lambda x: f"${x:,.0f}" if pd.notna(x) and x else "—")
        _stale["CHI"] = _stale.apply(
            lambda r: f"{CHI_EMOJI.get(r['chi_color'], '⚪')} {r['chi_score']}", axis=1
        )
        _stale_detail = _stale[["name", "owner_name", "ARR", "CHI", "industry"]].rename(
            columns={"name": "Account", "owner_name": "CSM", "industry": "Industry"}
        )
        _stale_total = pd.DataFrame([{
            "Account": "📊 Total",
            "CSM": f"{len(_stale)} accounts",
            "ARR": f"${_stale['_arr'].sum():,.0f}",
            "CHI": "", "Industry": "",
        }])
        st.dataframe(
            pd.concat([_stale_total, _stale_detail], ignore_index=True),
            use_container_width=True, hide_index=True, height=300
        )
    else:
        st.success("All accounts have active deals.")

    st.stop()

# ── Retention Analytics page ───────────────────────────────────────────────────
if _page == "🔄 Retention Analytics":
    from collections import defaultdict as _ddict

    st.subheader("🔄 Retention Analytics")
    st.caption(
        "Cohort-based GRR, Logo Retention, NRR, and Expansions from Campfire contract data. "
        "A **cohort** = all contracts whose end date falls in the selected period."
    )

    with st.spinner("Loading contract data..."):
        _ret_contracts = load_retention_contracts()
        _ret_accounts  = load_accounts()

    # Build CSM lookup: normalized Campfire name → HubSpot CSM
    _csm_by_cf_name = {campfire_normalize(a["name"]): a["owner_name"] for a in _ret_accounts}

    def _ret_csm(c):
        return (
            _csm_by_cf_name.get(campfire_normalize(c.get("client_name", "")))
            or c.get("csm_tag")
            or "—"
        )

    # ── Period selector ────────────────────────────────────────────────────────
    _ret_end_dates = [c["contract_end_date"] for c in _ret_contracts if c.get("contract_end_date")]
    _ret_quarters = sorted(
        {(date.fromisoformat(d).year, (date.fromisoformat(d).month - 1) // 3 + 1) for d in _ret_end_dates},
        reverse=True,
    )
    _ret_months = sorted(
        {(date.fromisoformat(d).year, date.fromisoformat(d).month) for d in _ret_end_dates},
        reverse=True,
    )
    _MNAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def _month_last(y, m):
        # Last calendar day of month m in year y
        return date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)

    _rp1, _rp2 = st.columns([2, 4])
    _ret_ptype = _rp1.selectbox("Period type", ["Quarter", "Month", "Custom Range"], key="ret_pt")

    if _ret_ptype == "Quarter":
        _ql = [f"Q{q} {yr}" for yr, q in _ret_quarters]
        _today_tup = (date.today().year, (date.today().month - 1) // 3 + 1)
        _dq = next((i for i, t in enumerate(_ret_quarters) if t == _today_tup), 0)
        _sel_ql = _rp2.selectbox("Quarter", _ql, index=_dq, key="ret_q")
        _py, _pq = _ret_quarters[_ql.index(_sel_ql)]
        _period_start = date(_py, (_pq - 1) * 3 + 1, 1)
        _period_end   = _month_last(_py, _pq * 3)
    elif _ret_ptype == "Month":
        _ml = [f"{_MNAMES[m - 1]} {yr}" for yr, m in _ret_months]
        _dm = next(
            (i for i, (y, m) in enumerate(_ret_months)
             if y == date.today().year and m == date.today().month), 0
        )
        _sel_ml = _rp2.selectbox("Month", _ml, index=_dm, key="ret_m")
        _py, _pm = _ret_months[_ml.index(_sel_ml)]
        _period_start = date(_py, _pm, 1)
        _period_end   = _month_last(_py, _pm)
    else:  # Custom Range
        _cc = _rp2.columns(2)
        _period_start = _cc[0].date_input("From", value=date(date.today().year, 1, 1), key="ret_from")
        _period_end   = _cc[1].date_input("To",   value=date.today(), key="ret_to")

    st.caption(
        f"Period: **{_period_start.strftime('%b %d, %Y')}** → **{_period_end.strftime('%b %d, %Y')}**"
    )

    # ── CSM filter ─────────────────────────────────────────────────────────────
    _ret_all_csms = sorted({_ret_csm(c) for c in _ret_contracts} - {"—"})
    _ret_csm_sel = st.selectbox("Filter by CSM", ["All CSMs"] + _ret_all_csms, key="ret_csm")
    st.divider()

    # ── Build cohort ───────────────────────────────────────────────────────────
    _RENEWAL_WINDOW = 90  # days after contract end to look for a subsequent contract

    # Index all contracts by client_id for renewal detection
    _by_client: dict = _ddict(list)
    for _c in _ret_contracts:
        if _c.get("client_id"):
            _by_client[_c["client_id"]].append(_c)
    for _cid in _by_client:
        _by_client[_cid].sort(key=lambda x: x.get("contract_start_date") or "")

    # Cohort = contracts ending in the selected period, filtered by CSM
    _cohort = []
    for _c in _ret_contracts:
        _ed = _c.get("contract_end_date")
        if not _ed:
            continue
        try:
            _edt = date.fromisoformat(_ed)
        except ValueError:
            continue
        if _period_start <= _edt <= _period_end:
            if _ret_csm_sel == "All CSMs" or _ret_csm(_c) == _ret_csm_sel:
                _cohort.append(_c)

    # Build per-contract detail rows
    _ret_today = date.today()
    _det_rows = []
    for _c in _cohort:
        _edt  = date.fromisoformat(_c["contract_end_date"])
        _cid  = _c.get("client_id")
        _ccsm = _ret_csm(_c)

        # Find renewal: next contract for same client starting within window after end date
        _renewal = None
        if _cid:
            for _oth in _by_client[_cid]:
                if _oth["contract_id"] == _c["contract_id"]:
                    continue
                _sd = _oth.get("contract_start_date")
                if not _sd:
                    continue
                try:
                    _sdt = date.fromisoformat(_sd)
                except ValueError:
                    continue
                if _edt < _sdt <= _edt + timedelta(days=_RENEWAL_WINDOW):
                    _renewal = _oth
                    break

        _prior_arr   = _c["arr"]
        _renewed_arr = _renewal["arr"] if _renewal else 0.0
        _expansion   = max(_renewed_arr - _prior_arr, 0.0) if _renewal else 0.0
        _contraction = max(_prior_arr - _renewed_arr, 0.0) if _renewal else 0.0

        if _renewal:
            if _expansion > 1:
                _st = "✅ Renewed + Expanded"
            elif _contraction > 1:
                _st = "🟡 Renewed (Contracted)"
            else:
                _st = "✅ Renewed"
        elif _edt > _ret_today:
            _st = "⏳ Not Yet Expired"
        elif (_ret_today - _edt).days <= 60:
            _st = "⚠️ Pending Renewal"
        else:
            _st = "❌ Churned"

        _det_rows.append({
            "Account":       _c["client_name"],
            "CSM":           _ccsm,
            "Contract End":  _c["contract_end_date"],
            "Prior ARR":     _prior_arr,
            "Prior TCV":     _c["total_contract_value"],
            "Status":        _st,
            "New Start":     _renewal["contract_start_date"] if _renewal else None,
            "New ARR":       _renewed_arr,
            "New TCV":       _renewal["total_contract_value"] if _renewal else 0.0,
            "Expansion $":   _expansion,
            "Contraction $": _contraction,
            "Billing":       _c["billing_frequency"],
            "Deal Name":     _c["deal_name"],
        })

    # ── Aggregate metrics ──────────────────────────────────────────────────────
    _n_cohort  = len(_det_rows)
    _n_renewed = sum(1 for r in _det_rows if r["Status"].startswith("✅"))
    _n_pending = sum(1 for r in _det_rows if "Pending" in r["Status"] or "Not Yet" in r["Status"])
    _n_churned = sum(1 for r in _det_rows if r["Status"].startswith("❌"))
    _n_decided = _n_cohort - _n_pending  # accounts whose renewal status is confirmed

    # GRR/NRR computed on decided accounts only (pending excluded from both num & denom)
    _decided_rows = [r for r in _det_rows if "Pending" not in r["Status"] and "Not Yet" not in r["Status"]]
    _prior_decided = sum(r["Prior ARR"] for r in _decided_rows)
    _prior_pending = sum(r["Prior ARR"] for r in _det_rows if "Pending" in r["Status"] or "Not Yet" in r["Status"])
    # Retained ARR = min(new, old) for each renewed account (expansions don't count toward GRR)
    _retained   = sum(min(r["New ARR"], r["Prior ARR"]) for r in _decided_rows if r["Status"].startswith("✅"))
    _exp_tot    = sum(r["Expansion $"] for r in _decided_rows)
    _churn_arr  = sum(r["Prior ARR"] for r in _decided_rows if r["Status"].startswith("❌"))

    _logo_ret = _n_renewed / _n_decided if _n_decided > 0 else 0
    _grr      = _retained / _prior_decided if _prior_decided else 0
    _nrr      = (_retained + _exp_tot) / _prior_decided if _prior_decided else 0

    # ── Summary tiles ──────────────────────────────────────────────────────────
    _t1, _t2, _t3, _t4, _t5, _t6 = st.columns(6)
    _t1.metric("Cohort",            f"{_n_cohort} contracts",
               delta=f"{_n_decided} decided · {_n_pending} pending" if _n_pending else None,
               delta_color="off")
    _t2.metric("🏢 Logo Retention",  f"{_logo_ret:.1%}",
               delta=f"{_n_renewed} renewed · {_n_churned} churned")
    _t3.metric("📉 GRR",             f"{_grr:.1%}",
               delta=f"${_retained:,.0f} of ${_prior_decided:,.0f}", delta_color="off")
    _t4.metric("📈 NRR",             f"{_nrr:.1%}",
               delta=f"+${_exp_tot:,.0f} expansion" if _exp_tot else "no expansion",
               delta_color="off")
    _t5.metric("🚀 Expansion ARR",   f"${_exp_tot:,.0f}",
               delta=f"+${_exp_tot / _prior_decided * 100:.1f}% uplift" if _prior_decided and _exp_tot else None,
               delta_color="off")
    _t6.metric("🔴 Churned ARR",     f"${_churn_arr:,.0f}",
               delta=f"{_n_churned} accts" if _n_churned else "—",
               delta_color="inverse" if _n_churned else "off")

    if _n_pending:
        st.info(
            f"ℹ️ **{_n_pending}** contract(s) (${_prior_pending:,.0f} ARR) are not yet expired "
            f"or within 60 days of expiry — shown as Pending/Not Yet Expired. "
            f"GRR, NRR, and Logo Retention are based on the **{_n_decided} decided** contracts only "
            "and will update as pending accounts resolve."
        )
    if not _n_cohort:
        st.warning("No contracts found with end dates in this period. Try a different period or date range.")

    st.divider()

    # ── By-CSM breakdown ──────────────────────────────────────────────────────
    st.markdown("#### By CSM")
    if _det_rows:
        _csm_buckets: dict = _ddict(list)
        for _r in _det_rows:
            _csm_buckets[_r["CSM"]].append(_r)

        _csm_rows = []
        for _cn, _rrs in sorted(_csm_buckets.items()):
            _cn_tot  = len(_rrs)
            _cn_ren  = sum(1 for r in _rrs if r["Status"].startswith("✅"))
            _cn_pnd  = sum(1 for r in _rrs if "Pending" in r["Status"] or "Not Yet" in r["Status"])
            _cn_dec  = _cn_tot - _cn_pnd  # decided accounts for this CSM
            # Prior ARR on decided accounts only (for GRR/NRR denominators)
            _cn_pa_dec = sum(r["Prior ARR"] for r in _rrs if "Pending" not in r["Status"] and "Not Yet" not in r["Status"])
            _cn_ra   = sum(min(r["New ARR"], r["Prior ARR"]) for r in _rrs if r["Status"].startswith("✅"))
            _cn_ex   = sum(r["Expansion $"] for r in _rrs if r["Status"].startswith("✅"))
            _cn_ch   = sum(r["Prior ARR"] for r in _rrs if r["Status"].startswith("❌"))
            _csm_rows.append({
                "CSM":           _cn,
                "Cohort":        _cn_tot,
                "Renewed":       _cn_ren,
                "Pending":       _cn_pnd,
                "Churned":       _cn_tot - _cn_ren - _cn_pnd,
                "Logo %":        f"{_cn_ren / _cn_dec:.0%}" if _cn_dec else "—",
                "Prior ARR":     f"${_cn_pa_dec:,.0f}",
                "Retained ARR":  f"${_cn_ra:,.0f}",
                "Expansion":     f"${_cn_ex:,.0f}" if _cn_ex else "—",
                "GRR":           f"{_cn_ra / _cn_pa_dec:.1%}" if _cn_pa_dec else "—",
                "NRR":           f"{(_cn_ra + _cn_ex) / _cn_pa_dec:.1%}" if _cn_pa_dec else "—",
                "Churned ARR":   f"${_cn_ch:,.0f}" if _cn_ch else "—",
            })
        st.dataframe(pd.DataFrame(_csm_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No contracts in this cohort.")

    st.divider()

    # ── Audit / raw data table ─────────────────────────────────────────────────
    with st.expander(f"📋 Cohort Detail — {_n_cohort} contracts (raw data for validation)", expanded=True):
        st.caption(
            f"Each row = one expiring contract in this period. "
            f"'Renewed' = a subsequent contract was found starting within {_RENEWAL_WINDOW} days of the end date. "
            "Use this table to cross-check against your Google Sheet."
        )
        if _det_rows:
            _aud_df = pd.DataFrame(_det_rows).copy()
            for _col in ["Prior ARR", "Prior TCV", "New ARR", "New TCV", "Expansion $", "Contraction $"]:
                _aud_df[_col] = _aud_df[_col].map(
                    lambda x: f"${x:,.0f}" if pd.notna(x) and x else "—"
                )
            _aud_df["New Start"] = _aud_df["New Start"].fillna("—")
            st.dataframe(
                _aud_df[[
                    "Account", "CSM", "Contract End", "Prior ARR", "Prior TCV",
                    "Status", "New Start", "New ARR", "New TCV",
                    "Expansion $", "Contraction $", "Billing", "Deal Name",
                ]],
                use_container_width=True,
                hide_index=True,
            )
            st.download_button(
                "⬇️ Download CSV",
                data=pd.DataFrame(_det_rows).to_csv(index=False),
                file_name=f"retention_{_period_start}_{_period_end}.csv",
                mime="text/csv",
            )
        else:
            st.info("No contracts found for this period.")

    st.stop()

# ── Priorities page ────────────────────────────────────────────────────────────
if _page == "🎯 Priorities":
    _pri_all = load_accounts()
    _pri_df  = pd.DataFrame(_pri_all)

    if _allowed_csms is not None:
        _pri_df = _pri_df[_pri_df["owner_name"].isin(_allowed_csms)]

    def _urgency(a):
        score = 0
        flags = []

        chi = a.get("chi_color", "")
        if chi == "red":
            score += 40
            flags.append(f"🔴 CHI Critical ({a.get('chi_score', '?')})")
        elif chi == "yellow":
            score += 15
            flags.append(f"🟡 CHI At Risk ({a.get('chi_score', '?')})")

        rs = a.get("risk_score", 0)
        if rs >= 4:
            score += 25
            flags.append(f"⚠️ High Churn Risk ({a.get('risk_label', '')})")

        dtr = a.get("days_to_renewal")
        if dtr is not None:
            if dtr < 0:
                score += 40
                flags.append(f"🔔 Renewal OVERDUE ({abs(int(dtr))}d ago)")
            elif dtr <= 30:
                score += 30
                flags.append(f"🔔 Renewing in {int(dtr)}d")
            elif dtr <= 60:
                score += 15
                flags.append(f"📅 Renewing in {int(dtr)}d")

        dlc = a.get("days_since_last_contact")
        if dlc is None:
            score += 20
            flags.append("📵 Never contacted")
        elif dlc >= 60:
            score += 20
            flags.append(f"📵 No contact in {int(dlc)}d")
        elif dlc >= 30:
            score += 10
            flags.append(f"📵 No contact in {int(dlc)}d")

        pendo = a.get("pendo_days_since_login")
        if pendo is None or pendo > 90:
            score += 10
            flags.append("🔒 No product login (90d+)")

        if a.get("campfire_has_overdue"):
            amt = a.get("campfire_overdue_amount") or 0
            score += 15
            flags.append(f"💳 Overdue invoice (${amt:,.0f})")

        return score, flags

    def _tier(score):
        if score >= 55: return "🚨 Act Now"
        if score >= 25: return "⚠️ This Week"
        if score >= 10: return "👀 Watch"
        return "✅ On Track"

    _pri_rows = []
    for _, _r in _pri_df.iterrows():
        _score, _flags = _urgency(_r.to_dict())
        _arr_val = _r.get("campfire_arr") or 0
        _pri_rows.append({
            "_score":    _score,
            "Tier":      _tier(_score),
            "Account":   _r["name"],
            "CSM":       _r["owner_name"],
            "CHI":       f"{CHI_EMOJI.get(_r['chi_color'], '⚪')} {_r['chi_score']}",
            "_chi_color":_r["chi_color"],
            "ARR":       f"${_arr_val:,.0f}" if _arr_val else "—",
            "_arr_raw":  _arr_val,
            "Renewal":   renewal_urgency(_r.get("days_to_renewal")),
            "Last Contact": contact_recency(_r.get("days_since_last_contact")),
            "Action Items": "  ·  ".join(_flags) if _flags else "✅ No flags",
        })
    _pri_rows.sort(key=lambda x: -x["_score"])
    _pri_table = pd.DataFrame(_pri_rows)

    # ── Header ──────────────────────────────────────────────────────────────────
    if _access["role"] == "csm":
        st.subheader(f"🎯 Your Priorities — {_access['display_name']}")
    else:
        st.subheader("🎯 Team Priorities")
    st.caption("Accounts ranked by urgency: CHI health score, churn risk, renewal timing, contact recency, invoice status.")

    # ── Summary tiles ────────────────────────────────────────────────────────────
    _t_act  = _pri_table[_pri_table["Tier"] == "🚨 Act Now"]
    _t_week = _pri_table[_pri_table["Tier"] == "⚠️ This Week"]
    _t_wtch = _pri_table[_pri_table["Tier"] == "👀 Watch"]
    _t_ok   = _pri_table[_pri_table["Tier"] == "✅ On Track"]

    _pc1, _pc2, _pc3, _pc4 = st.columns(4)
    _pc1.metric("🚨 Act Now",   len(_t_act),
                delta=f"${_t_act['_arr_raw'].sum():,.0f} ARR at stake" if len(_t_act) else None,
                delta_color="inverse")
    _pc2.metric("⚠️ This Week", len(_t_week),
                delta=f"${_t_week['_arr_raw'].sum():,.0f} ARR" if len(_t_week) else None,
                delta_color="inverse")
    _pc3.metric("👀 Watch",     len(_t_wtch),
                delta=f"${_t_wtch['_arr_raw'].sum():,.0f} ARR" if len(_t_wtch) else None,
                delta_color="off")
    _pc4.metric("✅ On Track",  len(_t_ok),
                delta=f"${_t_ok['_arr_raw'].sum():,.0f} ARR" if len(_t_ok) else None,
                delta_color="off")

    st.divider()

    # ── Management: CSM breakdown + filter ──────────────────────────────────────
    if _access["role"] != "csm":
        st.markdown("#### By CSM — Urgency Breakdown")
        _csm_bk = []
        for _cn in sorted(_pri_table["CSM"].unique()):
            _sub = _pri_table[_pri_table["CSM"] == _cn]
            _at_risk_arr = _sub[_sub["Tier"].isin(["🚨 Act Now", "⚠️ This Week"])]["_arr_raw"].sum()
            _csm_bk.append({
                "CSM":             _cn,
                "🚨 Act Now":      int((_sub["Tier"] == "🚨 Act Now").sum()),
                "⚠️ This Week":    int((_sub["Tier"] == "⚠️ This Week").sum()),
                "👀 Watch":        int((_sub["Tier"] == "👀 Watch").sum()),
                "✅ On Track":     int((_sub["Tier"] == "✅ On Track").sum()),
                "Accounts":        len(_sub),
                "Total ARR":       f"${_sub['_arr_raw'].sum():,.0f}",
                "At-Risk ARR":     f"${_at_risk_arr:,.0f}" if _at_risk_arr else "—",
            })
        _csm_bk_df = pd.DataFrame(_csm_bk).sort_values("🚨 Act Now", ascending=False)
        st.dataframe(_csm_bk_df, use_container_width=True, hide_index=True)
        st.divider()

        _pri_csm_sel = st.selectbox("Filter detail view by CSM", ["All CSMs"] + sorted(_pri_table["CSM"].unique().tolist()))
        _pri_show = _pri_table[_pri_table["CSM"] == _pri_csm_sel].copy() if _pri_csm_sel != "All CSMs" else _pri_table.copy()
    else:
        _pri_show = _pri_table.copy()

    # ── Tiered account lists ─────────────────────────────────────────────────────
    _detail_cols = ["Account", "CHI", "Renewal", "Last Contact", "ARR", "Action Items"]
    if _access["role"] != "csm":
        _detail_cols = ["Account", "CSM", "CHI", "Renewal", "Last Contact", "ARR", "Action Items"]

    for _tier_label in ["🚨 Act Now", "⚠️ This Week", "👀 Watch", "✅ On Track"]:
        _tier_rows = _pri_show[_pri_show["Tier"] == _tier_label]
        if len(_tier_rows) == 0:
            continue
        st.markdown(f"#### {_tier_label} — {len(_tier_rows)} account{'s' if len(_tier_rows) != 1 else ''}")
        st.dataframe(_tier_rows[_detail_cols], use_container_width=True, hide_index=True)
        st.divider()

    st.stop()

# ── Load data ──────────────────────────────────────────────────────────────────
accounts = load_accounts()
_all_df = pd.DataFrame(accounts)

# Split: Campfire-qualified accounts (main view) vs HubSpot-only audit list
df          = _all_df[_all_df["campfire_source"] == True].copy()
hs_audit_df = _all_df[_all_df["campfire_source"] == False].copy()

# Full dataset for peer benchmarks (before access restriction)
df_all_csms = df.copy()

# Restrict dataframe to what this user is allowed to see
if _allowed_csms is not None:
    df = df[df["owner_name"].isin(_allowed_csms)]

# ── Sidebar filters ────────────────────────────────────────────────────────────
st.sidebar.header("Filters")
st.sidebar.caption(f"Signed in as {_access['display_name']}")
st.sidebar.button("Sign out", on_click=st.logout, use_container_width=True)
st.sidebar.divider()

all_csms = sorted(df["owner_name"].dropna().unique().tolist())

# CSMs see only themselves — no filter needed; others get the picker
if _access["role"] == "csm":
    selected_csm = _access["hubspot_name"]
    st.sidebar.info(f"Showing your accounts: {selected_csm}")
else:
    selected_csm = st.sidebar.selectbox("CSM", ["All CSMs"] + all_csms)

risk_options = ["All", "High Risk (4-5)", "Medium Risk (3)", "Low Risk (1-2)", "Not Set"]
selected_risk = st.sidebar.selectbox("Risk Level", risk_options)

all_industries = sorted([i for i in df["industry"].dropna().unique().tolist() if i != "—"])
selected_industries = st.sidebar.multiselect("Industry", all_industries, placeholder="All Industries")

renewal_window = st.sidebar.selectbox(
    "Renewal Window",
    ["All", "Overdue", "Custom Days", "Next 30 Days", "Next 60 Days", "Next 90 Days", "No Deal"]
)

renewal_days = 30
if renewal_window == "Custom Days":
    renewal_days = st.sidebar.number_input("Days", min_value=1, max_value=365, value=30, step=1)

st.sidebar.divider()
# Load saved thresholds and seed session state on first render
_prefs = _load_prefs()
for _pk, _pv in _prefs.items():
    if _pk not in st.session_state:
        st.session_state[_pk] = _pv

response_threshold_hrs = st.sidebar.number_input(
    "Response threshold (hrs)", min_value=0.25, max_value=168.0, step=0.25,
    key="response_threshold_hrs",
    help="Used for '% responded under X hrs' column and summary stats."
)
response_threshold = int(response_threshold_hrs * 60)  # convert to minutes for comparisons
no_contact_days = st.sidebar.number_input(
    "No contact threshold (days)", min_value=1, max_value=365, step=5,
    key="no_contact_days",
    help="Used for 'No Contact Xd+' metric tile, CSM summary column, and Needs Attention flags."
)
pendo_window = st.sidebar.number_input(
    "Pendo login window (days)", min_value=1, max_value=365, step=15,
    key="pendo_window",
    help="Window for Pendo login metrics: % no login, active users, unique users per account."
)
# Auto-save when thresholds change
_cur_prefs = {
    "response_threshold_hrs": float(response_threshold_hrs),
    "no_contact_days": int(no_contact_days),
    "pendo_window": int(pendo_window),
}
if _cur_prefs != _prefs:
    _save_prefs(_cur_prefs)

st.sidebar.divider()
campfire_status_filter = st.sidebar.selectbox(
    "Campfire Status",
    ["Active + Recently Churned", "Active Only", "Churned (last 12mo) Only"],
    help="Filter by Campfire contract status. Default shows both active and recently churned clients."
)
show_stale = st.sidebar.checkbox("Show only accounts without active HubSpot deal", value=False)
hide_stale = st.sidebar.checkbox("Hide accounts without active HubSpot deal", value=False)

chi_filter = st.sidebar.selectbox(
    "CHI Health", ["All", "🟢 Healthy (65-100)", "🟡 At Risk (35-64)", "🔴 Critical (0-34)"]
)
invoice_filter = st.sidebar.selectbox(
    "Invoice Status", ["All", "Has Open Invoice", "Has Overdue Invoice (30d+)"]
)

no_contact_window = st.sidebar.selectbox(
    "No Contact Since",
    ["All Accounts", "No contact in 30+ days", "No contact in 60+ days", "No contact in 90+ days"],
)

# ── Apply filters ──────────────────────────────────────────────────────────────
filtered = df.copy()

if selected_csm != "All CSMs":
    filtered = filtered[filtered["owner_name"] == selected_csm]

if selected_risk == "High Risk (4-5)":
    filtered = filtered[filtered["risk_score"].isin([4, 5])]
elif selected_risk == "Medium Risk (3)":
    filtered = filtered[filtered["risk_score"] == 3]
elif selected_risk == "Low Risk (1-2)":
    filtered = filtered[filtered["risk_score"].isin([1, 2])]
elif selected_risk == "Not Set":
    filtered = filtered[filtered["risk_score"] == 0]

if selected_industries:
    filtered = filtered[filtered["industry"].isin(selected_industries)]

if campfire_status_filter == "Active Only":
    filtered = filtered[filtered["campfire_account_status"] == "ACTIVE"]
elif campfire_status_filter == "Churned (last 12mo) Only":
    filtered = filtered[filtered["campfire_account_status"] == "CHURNED_RECENT"]

if show_stale:
    filtered = filtered[filtered["possibly_stale"] == True]
elif hide_stale:
    filtered = filtered[filtered["possibly_stale"] == False]

if no_contact_window == "No contact in 30+ days":
    filtered = filtered[filtered["days_since_last_contact"].isna() | (filtered["days_since_last_contact"] >= 30)]
elif no_contact_window == "No contact in 60+ days":
    filtered = filtered[filtered["days_since_last_contact"].isna() | (filtered["days_since_last_contact"] >= 60)]
elif no_contact_window == "No contact in 90+ days":
    filtered = filtered[filtered["days_since_last_contact"].isna() | (filtered["days_since_last_contact"] >= 90)]

if chi_filter == "🟢 Healthy (65-100)":
    filtered = filtered[filtered["chi_color"] == "green"]
elif chi_filter == "🟡 At Risk (35-64)":
    filtered = filtered[filtered["chi_color"] == "yellow"]
elif chi_filter == "🔴 Critical (0-34)":
    filtered = filtered[filtered["chi_color"] == "red"]

if invoice_filter == "Has Open Invoice":
    filtered = filtered[filtered["campfire_open_invoice_count"] > 0]
elif invoice_filter == "Has Overdue Invoice (30d+)":
    filtered = filtered[filtered["campfire_has_overdue"] == True]

if renewal_window == "Overdue":
    filtered = filtered[filtered["days_to_renewal"].notna() & (filtered["days_to_renewal"] < 0)]
elif renewal_window in ("Next 30 Days", "Custom Days"):
    filtered = filtered[filtered["days_to_renewal"].notna() & (filtered["days_to_renewal"] <= renewal_days) & (filtered["days_to_renewal"] >= 0)]
elif renewal_window == "Next 60 Days":
    filtered = filtered[filtered["days_to_renewal"].notna() & (filtered["days_to_renewal"] <= 60) & (filtered["days_to_renewal"] >= 0)]
elif renewal_window == "Next 90 Days":
    filtered = filtered[filtered["days_to_renewal"].notna() & (filtered["days_to_renewal"] <= 90) & (filtered["days_to_renewal"] >= 0)]
elif renewal_window == "No Deal":
    filtered = filtered[filtered["days_to_renewal"].isna()]

# ── Precompute all metric values ───────────────────────────────────────────────
high_risk            = filtered[filtered["risk_score"].isin([4, 5])]
chi_critical         = filtered[filtered["chi_color"] == "red"]
renewing_30          = filtered[filtered["days_to_renewal"].notna() & (filtered["days_to_renewal"] <= renewal_days) & (filtered["days_to_renewal"] >= 0)]
renewing_90          = filtered[filtered["days_to_renewal"].notna() & (filtered["days_to_renewal"] <= 90) & (filtered["days_to_renewal"] >= 0)]
overdue              = filtered[filtered["days_to_renewal"].notna() & (filtered["days_to_renewal"] < 0)]
stale                = filtered[filtered["possibly_stale"] == True]
total_arr            = filtered["campfire_arr"].sum()
outstanding_accounts = filtered[filtered["campfire_has_overdue"] == True]
chi_critical_arr     = chi_critical["campfire_arr"].fillna(0).sum()
renewing_90_arr      = renewing_90["campfire_arr"].fillna(0).sum()
overdue_inv_total    = outstanding_accounts["campfire_overdue_amount"].fillna(0).sum()

all_response_times = [t for times in filtered["response_times"] for t in (times if isinstance(times, list) else [])]
if all_response_times:
    _rt_sorted = sorted(all_response_times)
    _n = len(_rt_sorted)
    _mid = _n // 2
    overall_median = _rt_sorted[_mid] if _n % 2 else (_rt_sorted[_mid - 1] + _rt_sorted[_mid]) / 2
    pct_under = round(len([t for t in all_response_times if t <= response_threshold]) / _n * 100)
else:
    overall_median = None
    pct_under = None
no_contact_30 = int((filtered["days_since_last_contact"].isna() | (filtered["days_since_last_contact"] >= no_contact_days)).sum())

now_ms = int(datetime.now().timestamp() * 1000)
pendo_cutoff_ms = now_ms - pendo_window * 24 * 3600 * 1000

def _users_in_window(ts_list, cutoff_ms):
    if not isinstance(ts_list, list):
        return 0
    return len([t for t in ts_list if t >= cutoff_ms])

pendo_no_login          = int((filtered["pendo_days_since_login"].isna() | (filtered["pendo_days_since_login"] > pendo_window)).sum())
pendo_pct_no_login      = round(pendo_no_login / len(filtered) * 100) if len(filtered) else 0
pendo_total_users       = int(filtered["pendo_visitor_ts"].apply(lambda ts: _users_in_window(ts, pendo_cutoff_ms)).sum())
pendo_accounts_with_login = int((filtered["pendo_days_since_login"] <= pendo_window).sum())

# ── Pre-compute drill-down subsets ─────────────────────────────────────────────
no_contact_accts = filtered[
    filtered["days_since_last_contact"].isna() | (filtered["days_since_last_contact"] >= no_contact_days)
]
no_login_accts = filtered[
    filtered["pendo_days_since_login"].isna() | (filtered["pendo_days_since_login"] > pendo_window)
]
with_login_accts = filtered[filtered["pendo_days_since_login"] <= pendo_window]

# Drill-down map: key → (subset DataFrame, display title)
_DRILL_MAP = {
    "total":            (filtered,            "All Accounts"),
    "chi_critical":     (chi_critical,        "🔴 CHI Critical Accounts (score 0–34)"),
    "high_risk":        (high_risk,           "⚠️ High Churn Risk Accounts"),
    "overdue_renewals": (overdue,             "🔔 Overdue Renewals"),
    "renewing_90":      (renewing_90,         "🗓 Renewing in ≤90 Days"),
    "overdue_invoices": (outstanding_accounts,"💳 Accounts with Overdue Invoices"),
    "no_contact":       (no_contact_accts,    f"📧 No Contact in {no_contact_days}+ Days"),
    "stale":            (stale,               "🧹 Accounts without Active HubSpot Deal"),
    "no_login":         (no_login_accts,      f"🔒 No Pendo Login in {pendo_window}+ Days"),
    "with_login":       (with_login_accts,    f"✅ Accounts with Pendo Login in {pendo_window}d"),
}

# Label → internal key mapping (used by both tile buttons and the drill selectbox)
_DRILL_LABELS = {
    "All Accounts":                       "total",
    "CHI Critical":                       "chi_critical",
    "High Risk":                          "high_risk",
    "Overdue Renewals":                   "overdue_renewals",
    "Renewing ≤90d":                      "renewing_90",
    "Overdue Invoices":                   "overdue_invoices",
    f"No Contact {no_contact_days}d+":    "no_contact",
    "Possibly Stale":                     "stale",
    f"No Pendo Login {pendo_window}d+":   "no_login",
    "With Pendo Login":                   "with_login",
}

# Drill state: plain session-state key (NOT a widget key — no widget conflict)
if "active_drill" not in st.session_state:
    st.session_state["active_drill"] = None

def _set_drill(key):
    st.session_state["active_drill"] = None if st.session_state["active_drill"] == key else key

# ── Drill-down panel (renders above metrics so it's immediately visible on click)
_drill_key = st.session_state["active_drill"]
if _drill_key and _drill_key in _DRILL_MAP:
    _dd_df, _dd_title = _DRILL_MAP[_drill_key]
    _dd_hdr, _dd_close_col = st.columns([5, 1])
    _dd_hdr.markdown(f"##### 📋 {_dd_title} — {len(_dd_df)} account{'s' if len(_dd_df) != 1 else ''}")
    if _dd_close_col.button("✕ Close", key="btn_close_drill"):
        st.session_state["active_drill"] = None
        st.rerun()
    _dd_rows = []
    for _, _r in _dd_df.iterrows():
        _dd_rows.append({
            "Account":        _r["name"],
            "CSM":            _r["owner_name"],
            "CHI":            f"{CHI_EMOJI.get(_r.get('chi_color',''), '⚪')} {_r.get('chi_score', '—')}",
            "Risk":           _r.get("risk_label", "—"),
            "ARR (Campfire)": f"${_r['campfire_arr']:,.0f}" if pd.notna(_r.get("campfire_arr")) and _r.get("campfire_arr") else "—",
            "Renewal":        renewal_urgency(_r.get("days_to_renewal")),
            "Last Contact":   contact_recency(_r.get("days_since_last_contact")),
            "Last Login":     (f"{int(_r['pendo_days_since_login'])}d ago"
                              if pd.notna(_r.get("pendo_days_since_login")) and _r.get("pendo_days_since_login") is not None
                              else "—"),
            "Meetings (90d)": int(_r.get("meeting_count_90d") or 0),
            "Deal Stage":     _r.get("deal_stage", "—"),
            "Industry":       _r.get("industry", "—"),
        })
    _dd_show = pd.DataFrame(_dd_rows)
    st.dataframe(_dd_show, use_container_width=True, hide_index=True, height=350)
    st.download_button(
        "⬇️ Download CSV",
        data=_dd_show.to_csv(index=False),
        file_name=f"chi_drill_{_drill_key}.csv",
        mime="text/csv",
        key="btn_dd_download",
    )
    st.divider()

# ─ Section 1: Portfolio Health ─────────────────────────────────────────────────
st.markdown("#### 🏥 Portfolio Health")
_ph1, _ph2, _ph3, _ph4 = st.columns(4)

_ph1.metric("Total Accounts", len(filtered), help="All Campfire-qualified accounts visible to your filters")
if _ph1.button("↗ view", key="btn_total"):
    _set_drill("total")
_ph2.metric("🔴 CHI Critical", len(chi_critical),
            delta=f"${chi_critical_arr:,.0f} ARR at risk" if chi_critical_arr else None, delta_color="inverse")
if _ph2.button("↗ view", key="btn_chi_critical"):
    _set_drill("chi_critical")
_ph3.metric("⚠️ High Risk", len(high_risk),
            delta=f"{round(len(high_risk)/len(filtered)*100)}% of portfolio" if len(filtered) else None,
            delta_color="inverse")
if _ph3.button("↗ view", key="btn_high_risk"):
    _set_drill("high_risk")
_ph4.metric("🔔 Overdue Renewals", len(overdue),
            delta=f"+ {len(renewing_90)} renewing ≤90d" if len(renewing_90) else None, delta_color="inverse")
if _ph4.button("↗ view", key="btn_overdue"):
    _set_drill("overdue_renewals")

# ─ Section 2: Revenue ──────────────────────────────────────────────────────────
st.markdown("#### 💰 Revenue")
_rv1, _rv2, _rv3, _rv4 = st.columns(4)

_rv1.metric("Total ARR (Campfire)", f"${total_arr:,.0f}" if total_arr else "—")
if _rv1.button("↗ view", key="btn_arr_total"):
    _set_drill("total")
_rv2.metric("CHI Critical ARR", f"${chi_critical_arr:,.0f}" if chi_critical_arr else "—",
            delta=f"{round(chi_critical_arr/total_arr*100,1)}% of total" if total_arr and chi_critical_arr else None,
            delta_color="inverse")
if _rv2.button("↗ view", key="btn_chi_arr"):
    _set_drill("chi_critical")
_rv3.metric("Renewing ≤90d ARR", f"${renewing_90_arr:,.0f}" if renewing_90_arr else "—",
            delta=f"{len(renewing_90)} accounts", delta_color="off")
if _rv3.button("↗ view", key="btn_renewing_arr"):
    _set_drill("renewing_90")
_rv4.metric("💳 Overdue Invoices", f"${overdue_inv_total:,.0f}" if overdue_inv_total else "—",
            delta=f"{len(outstanding_accounts)} accounts" if len(outstanding_accounts) else None,
            delta_color="inverse")
if _rv4.button("↗ view", key="btn_overdue_inv"):
    _set_drill("overdue_invoices")

# ─ Section 3: Team Activity ────────────────────────────────────────────────────
_act_hdr, _act_sel = st.columns([3, 1])
_act_hdr.markdown("#### 📞 Team Activity")
meeting_period_label = _act_sel.selectbox(
    "Meeting period", ["Last 7 days", "Last 14 days", "Last 30 days", "Last 90 days"],
    index=3, label_visibility="collapsed",
    help="Controls meeting count and avg/week tiles below"
)
_mtg_days = {"Last 7 days": 7, "Last 14 days": 14, "Last 30 days": 30, "Last 90 days": 90}[meeting_period_label]
_owner_ids_key = tuple(sorted(str(x) for x in filtered["owner_id"].dropna().unique().tolist()))
_mtg = load_team_meetings(_mtg_days, _owner_ids_key)

_act1, _act2, _act3, _act4 = st.columns(4)
_act1.metric(f"📅 Meetings ({meeting_period_label})", _mtg["total"],
             delta=f"This wk: {_mtg['this_week']} · Last wk: {_mtg['last_week']}", delta_color="off")
_act2.metric("📅 Avg Meetings / Week", _mtg["avg_per_week"],
             help=f"Over the selected {_mtg_days}-day window")
_act3.metric("📅 Upcoming Scheduled", _mtg["upcoming_90d"],
             help="Meetings scheduled in HubSpot for the next 90 days")
_act4.metric(f"📧 No Contact {no_contact_days}d+", no_contact_30)
if _act4.button("↗ view", key="btn_no_contact"):
    _set_drill("no_contact")

_rs1, _rs2, _rs3, _rs4 = st.columns(4)
_rs1.metric("⏱ Median Response", f"{int(overall_median)} min" if overall_median else "—")
_rs2.metric(f"✅ Responded < {response_threshold_hrs}hr", f"{pct_under}%" if pct_under is not None else "—")
_rs3.metric("🤝 Total Touchpoints (90d)", f"{int(filtered['total_touchpoints_90d'].sum())}")
_rs4.metric("🧹 Possibly Stale", len(stale), help="Accounts with no active HubSpot deal — may be churned")
if _rs4.button("↗ view", key="btn_stale"):
    _set_drill("stale")

# ─ Section 4: Product Engagement ───────────────────────────────────────────────
st.markdown(f"#### 📱 Product Engagement  ({pendo_window}d window)")
_pe1, _pe2, _pe3, _pe4 = st.columns(4)

_pe1.metric("🔒 No Login", pendo_no_login,
            help=f"{pendo_pct_no_login}% of accounts have no Pendo login in {pendo_window} days")
if _pe1.button("↗ view", key="btn_no_login"):
    _set_drill("no_login")
_pe2.metric("% No Login", f"{pendo_pct_no_login}%")
_pe3.metric("👥 Unique Users", pendo_total_users,
            help="Sum of unique users across all filtered accounts in the window")
_pe4.metric("✅ Accounts w/ Login", pendo_accounts_with_login)
if _pe4.button("↗ view", key="btn_with_login"):
    _set_drill("with_login")

st.divider()

# ── No Campfire ARR audit list ─────────────────────────────────────────────────
# ── HubSpot audit list: customers not matched to any active/recent Campfire account ──
# (hs_audit_df is computed at load time from the full account list with campfire_source=False)
_hs_audit_visible = hs_audit_df  # unfiltered by CSM/risk — show all unmatched regardless
if len(_hs_audit_visible):
    with st.expander(
        f"⚠️ {len(_hs_audit_visible)} HubSpot 'customer' accounts not in Campfire — click to audit & clean up",
        expanded=False
    ):
        st.caption(
            "These HubSpot accounts are tagged 'customer' but have no active or recently-churned Campfire contract. "
            "They are excluded from the account list and all metrics above. "
            "For each: update HubSpot lifecycle to 'Former Customer' if they've churned, "
            "or link them to a Campfire contract via the Data Mapping page."
        )
        _audit_rows = []
        for _, _r in _hs_audit_visible.iterrows():
            _audit_rows.append({
                "Account":    _r["name"],
                "CSM":        _r["owner_name"],
                "Industry":   _r.get("industry", "—"),
                "Deal Stage": _r.get("deal_stage", "—"),
                "Renewal":    renewal_urgency(_r.get("days_to_renewal")),
                "Action":     "Update lifecycle → Former Customer" if _r.get("possibly_stale") else "Link to Campfire via Data Mapping",
            })
        _audit_df = pd.DataFrame(_audit_rows).sort_values("Account")
        st.dataframe(_audit_df, use_container_width=True, hide_index=True)
        st.download_button(
            "⬇️ Download CSV",
            data=_audit_df.to_csv(index=False),
            file_name="hubspot_accounts_not_in_campfire.csv",
            mime="text/csv",
        )

st.divider()

# ── Account table ──────────────────────────────────────────────────────────────
st.subheader(f"Accounts ({len(filtered)})")

display = filtered[[
    "id", "name", "owner_name", "risk_score", "risk_label",
    "risk_reasons", "industry", "renewal_date", "days_to_renewal", "arr", "deal_stage", "tier",
    "possibly_stale", "campfire_account_status",
    "days_since_last_contact", "days_since_any_email", "days_since_outbound_email",
    "days_since_meeting", "email_count_90d", "outbound_count_90d", "meeting_count_90d",
    "total_touchpoints_90d", "median_response_minutes", "response_times",
    "pendo_days_since_login", "pendo_visitor_ts",
    "campfire_arr", "campfire_open_invoice_count", "campfire_open_amount",
    "campfire_days_open", "campfire_has_overdue",
    "campfire_overdue_invoice_count", "campfire_overdue_amount", "campfire_days_overdue",
    "chi_score", "chi_color", "chi_label", "chi_breakdown",
    "news_count", "news_articles",
]].copy()

display["Risk"] = display.apply(lambda r: f"{RISK_EMOJI.get(r['risk_score'], '⚪')} {r['risk_label']}", axis=1)
display["Renewal"] = display["days_to_renewal"].map(renewal_urgency)
display["Renewal Deal ARR"] = display["arr"].map(lambda x: f"${x:,.0f}" if pd.notna(x) and x else "—")
display["ARR (Campfire)"] = display["campfire_arr"].map(lambda x: f"${x:,.0f}" if pd.notna(x) and x else "—")
display["Status"] = display["campfire_account_status"].map(
    lambda s: "🔴 Churned" if s == "CHURNED_RECENT" else ("🟢 Active" if s == "ACTIVE" else "—")
)
def fmt_open_invoices(r):
    count = int(r["campfire_open_invoice_count"] or 0)
    if count == 0:
        return "—"
    amt = r["campfire_open_amount"] or 0
    days = r["campfire_days_open"] or 0
    overdue = int(r["campfire_overdue_invoice_count"] or 0)
    flag = "⚠️" if overdue > 0 else "💳"
    inv_label = f"{count} inv"
    days_label = f"{int(days)}d open" if days >= 0 else ""
    overdue_label = f"{overdue} overdue" if overdue > 0 else ""
    parts = [p for p in [inv_label, days_label, overdue_label] if p]
    return f"{flag} ${amt:,.0f} ({', '.join(parts)})"

display["Open Invoices"] = display.apply(fmt_open_invoices, axis=1)

display["CHI"] = display.apply(
    lambda r: f"{CHI_EMOJI.get(r['chi_color'], '⚪')} {r['chi_score']} {r['chi_label']}", axis=1
)

def fmt_news(articles):
    if not isinstance(articles, list) or not articles:
        return "—"
    tags = [a.get("tag", "news") for a in articles]
    acq = tags.count("acquisition")
    lead = tags.count("leadership")
    parts = []
    if acq:
        parts.append(f"{acq} 🤝")
    if lead:
        parts.append(f"{lead} 👤")
    other = len(tags) - acq - lead
    if other:
        parts.append(f"{other} 📰")
    return " · ".join(parts) if parts else f"{len(articles)} 📰"

display["News"] = display["news_articles"].apply(fmt_news)
display["Flag"] = display["possibly_stale"].map(lambda x: "🧹 No Deal" if x else "")
display["Last Contact"] = display["days_since_last_contact"].map(contact_recency)

def pendo_login_recency(days):
    if days is None:
        return "—"
    if days <= 30:
        return f"🟢 {days}d"
    if days <= 60:
        return f"🟡 {days}d"
    if days <= 90:
        return f"🟠 {days}d"
    return f"🔴 {days}d"

display["Last Login (Pendo)"] = display["pendo_days_since_login"].map(pendo_login_recency)
display[f"Users ({pendo_window}d)"] = display["pendo_visitor_ts"].apply(
    lambda ts: str(_users_in_window(ts, pendo_cutoff_ms)) if isinstance(ts, list) and _users_in_window(ts, pendo_cutoff_ms) > 0 else "—"
)

def pct_under_threshold(times, threshold):
    if not isinstance(times, list) or len(times) == 0:
        return "—"
    pct = round(len([t for t in times if t <= threshold]) / len(times) * 100)
    return f"{pct}% ({len(times)})"

display[f"< {response_threshold_hrs}hr"] = display["response_times"].apply(
    lambda t: pct_under_threshold(t, response_threshold)
)

display = display.rename(columns={
    "id": "HubSpot ID",
    "name": "Account",
    "owner_name": "CSM",
    "risk_reasons": "Risk Reasons",
    "industry": "Industry",
    "renewal_date": "Renewal Date",
    "deal_stage": "Deal Stage",
    "tier": "Tier",
    "days_since_last_contact": "Last Contact (days)",
    "days_since_any_email": "Last Email (days)",
    "days_since_outbound_email": "Last Outbound (days)",
    "days_since_meeting": "Last Meeting (days)",
    "email_count_90d": "Emails (90d)",
    "outbound_count_90d": "Sent (90d)",
    "meeting_count_90d": "Meetings (90d)",
    "total_touchpoints_90d": "Total Contacts (90d)",
    "median_response_minutes": "Median Response (min)",
})

display = display.sort_values(["chi_score", "risk_score"], ascending=[True, False])

st.dataframe(
    display[["Status", "Flag", "CHI", "Account", "CSM", "Risk", "Risk Reasons", "Industry",
             "Last Contact", "Last Email (days)", "Last Outbound (days)", "Last Meeting (days)",
             "Median Response (min)", f"< {response_threshold_hrs}hr",
             "Total Contacts (90d)", "Emails (90d)", "Sent (90d)", "Meetings (90d)",
             "Last Login (Pendo)", f"Users ({pendo_window}d)",
             "Renewal Date", "Renewal", "Renewal Deal ARR", "ARR (Campfire)", "Open Invoices",
             "News", "Deal Stage", "Tier"]],
    use_container_width=True,
    hide_index=True,
    height=600,
)

# ── Account Detail ─────────────────────────────────────────────────────────────
st.divider()
with st.expander("🔍 Account Detail — click to look up any account", expanded=False):
    _acct_names = sorted(filtered["name"].dropna().tolist())
    _sel_acct = st.selectbox("Select account", ["— choose —"] + _acct_names, key="acct_detail_sel")
    if _sel_acct and _sel_acct != "— choose —":
        _ad = filtered[filtered["name"] == _sel_acct].iloc[0].to_dict()
        _ad_c1, _ad_c2, _ad_c3 = st.columns(3)

        # CHI score card
        _chi_color = _ad.get("chi_color", "")
        _chi_emoji = CHI_EMOJI.get(_chi_color, "⚪")
        _ad_c1.markdown(f"### {_chi_emoji} CHI Score: **{_ad.get('chi_score', '—')}**")
        _ad_c1.caption(_ad.get("chi_label", ""))
        _breakdown = _ad.get("chi_breakdown") or {}
        if _breakdown:
            _bd_rows = []
            _labels = {
                "churn_risk": ("🔴 Churn Risk", 30),
                "pendo":      ("📱 Product Login", 25),
                "contact":    ("📧 Contact Recency", 25),
                "invoice":    ("💳 Invoice Health", 10),
                "renewal":    ("📅 Renewal Proximity", 10),
            }
            for _k, (_lbl, _max) in _labels.items():
                _pts = _breakdown.get(_k, 0)
                _bar = "█" * int(_pts / _max * 10) + "░" * (10 - int(_pts / _max * 10))
                _bd_rows.append({"Signal": _lbl, "Points": f"{_pts}/{_max}", "Bar": _bar})
            _ad_c1.dataframe(pd.DataFrame(_bd_rows), hide_index=True, use_container_width=True)

        # Contact & activity
        _ad_c2.markdown("### 📧 Activity")
        _ad_c2.metric("Last Contact",  contact_recency(_ad.get("days_since_last_contact")))
        _ad_c2.metric("Last Meeting",  f"{int(_ad['days_since_meeting'])}d ago" if _ad.get("days_since_meeting") else "—")
        _ad_c2.metric("Emails (90d)",  int(_ad.get("email_count_90d", 0)))
        _ad_c2.metric("Meetings (90d)", int(_ad.get("meeting_count_90d", 0)))
        _pendo = _ad.get("pendo_days_since_login")
        _ad_c2.metric("Last Product Login", f"{int(_pendo)}d ago" if _pendo else "Never logged in")

        # Financials & renewal
        _ad_c3.markdown("### 💰 Account")
        _ad_c3.metric("ARR (Campfire)", f"${_ad['campfire_arr']:,.0f}" if _ad.get("campfire_arr") else "—")
        _ad_c3.metric("Renewal Deal ARR", f"${_ad['arr']:,.0f}" if _ad.get("arr") else "—")
        _ad_c3.metric("Renewal", renewal_urgency(_ad.get("days_to_renewal")))
        _ad_c3.metric("Deal Stage", _ad.get("deal_stage", "—"))
        _ad_c3.metric("CSM Risk Rating", f"{RISK_EMOJI.get(_ad.get('risk_score',0),'⚪')} {_ad.get('risk_label','—')}")

        # Invoice status
        if _ad.get("campfire_open_invoice_count", 0) > 0:
            st.markdown("**💳 Invoice Status**")
            _inv_c1, _inv_c2, _inv_c3, _inv_c4 = st.columns(4)
            _inv_c1.metric("Open Invoices", int(_ad["campfire_open_invoice_count"]))
            _inv_c2.metric("Open Amount", f"${_ad.get('campfire_open_amount', 0):,.0f}")
            _inv_c3.metric("Days Open", f"{int(_ad['campfire_days_open'])}d" if _ad.get("campfire_days_open") else "—")
            if _ad.get("campfire_has_overdue"):
                _inv_c4.metric("⚠️ Overdue Amount", f"${_ad.get('campfire_overdue_amount', 0):,.0f}",
                               delta=f"{int(_ad.get('campfire_days_overdue', 0))}d past due", delta_color="inverse")

        # Risk reasons
        if _ad.get("risk_reasons") and _ad["risk_reasons"] != "—":
            st.markdown(f"**⚠️ Risk Reasons:** {_ad['risk_reasons']}")

        # News articles
        _news = _ad.get("news_articles")
        if isinstance(_news, list) and _news:
            st.markdown("**📰 Recent News**")
            for _art in _news[:5]:
                _tag = _art.get("tag", "news")
                _tag_icon = {"acquisition": "🤝", "leadership": "👤"}.get(_tag, "📰")
                st.markdown(f"- {_tag_icon} [{_art.get('title', 'Article')}]({_art.get('url', '#')})  "
                            f"<small>{_art.get('published', '')}</small>", unsafe_allow_html=True)

# ── News refresh ───────────────────────────────────────────────────────────────
with st.expander("📰 Refresh Account News", expanded=False):
    st.caption("Fetches recent headlines from Google News for each account. Cached 7 days. Takes ~2 min for all accounts.")
    _news_col1, _news_col2 = st.columns([2, 4])
    if _news_col1.button("🔄 Refresh News for Visible Accounts"):
        _names = filtered["name"].dropna().tolist()
        with st.spinner(f"Fetching news for {len(_names)} accounts..."):
            refresh_news_for_accounts(_names)
        st.success("News refreshed. Reload the page to see updates.")

# ── Needs Attention digest ─────────────────────────────────────────────────────
st.divider()
st.subheader("⚠️ Needs Attention")
st.caption(f"Accounts flagged on 2+ signals: CHI Critical, High Risk, No Contact {no_contact_days}d+, Overdue Invoice, No Login 90d+")

def _attention_flags(r):
    flags = []
    if r.get("chi_color") == "red":
        flags.append("🔴 CHI Critical")
    if r.get("risk_score", 0) >= 4:
        flags.append("⚠️ High Risk")
    contact = r.get("days_since_last_contact")
    if contact is None or contact >= no_contact_days:
        flags.append(f"📵 No Contact {no_contact_days}d+")
    if r.get("campfire_has_overdue"):
        flags.append("💳 Overdue Invoice")
    pendo = r.get("pendo_days_since_login")
    if pendo is None or pendo > 90:
        flags.append("🔒 No Pendo Login")
    return flags

_attention_rows = []
for _, _row in filtered.iterrows():
    _flags = _attention_flags(_row)
    if len(_flags) >= 2:
        _attention_rows.append({
            "Account": _row["name"],
            "CSM": _row["owner_name"],
            "CHI": f"{CHI_EMOJI.get(_row['chi_color'], '⚪')} {_row['chi_score']}",
            "Signals": "  ·  ".join(_flags),
            "ARR": f"${_row['campfire_arr']:,.0f}" if _row.get("campfire_arr") else "—",
        })

if _attention_rows:
    _attn_df = pd.DataFrame(_attention_rows).sort_values("CHI")
    st.dataframe(_attn_df, use_container_width=True, hide_index=True)
else:
    st.success("No accounts flagged on multiple signals right now.")

# ── CSM summary (leadership view) ─────────────────────────────────────────────
if selected_csm == "All CSMs" and _access["role"] != "csm":
    st.divider()
    st.subheader("CSM Summary")

    def flatten_times(series):
        return [t for times in series for t in (times if isinstance(times, list) else [])]

    csm_summary = filtered.groupby("owner_name").agg(
        Accounts=("name", "count"),
        High_Risk=("risk_score", lambda x: (x >= 4).sum()),
        Renewing_30d=("days_to_renewal", lambda x: ((x >= 0) & (x <= 30)).sum()),
        Total_ARR=("campfire_arr", lambda x: x.sum()),
        No_Contact_30d=("days_since_last_contact", lambda x: (x.isna() | (x >= no_contact_days)).sum()),
        Avg_Last_Contact=("days_since_last_contact", lambda x: round(x.mean(), 0) if x.notna().any() else None),
        Total_Touchpoints=("total_touchpoints_90d", "sum"),
        All_Response_Times=("response_times", flatten_times),
    ).reset_index()

    def csm_median(times):
        if not times:
            return None
        s = sorted(times)
        n = len(s)
        mid = n // 2
        return round(s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2, 0)

    def csm_pct_under(times, threshold):
        if not times:
            return None
        return round(len([t for t in times if t <= threshold]) / len(times) * 100)

    csm_summary["Median Response (min)"] = csm_summary["All_Response_Times"].apply(csm_median)
    csm_summary[f"< {response_threshold_hrs}hr"] = csm_summary["All_Response_Times"].apply(
        lambda t: csm_pct_under(t, response_threshold)
    )

    csm_summary = csm_summary.drop(columns=["All_Response_Times"])
    csm_summary = csm_summary.rename(columns={
        "owner_name": "CSM",
        "High_Risk": "High Risk",
        "Renewing_30d": "Renewing (30d)",
        "Total_ARR": "Total ARR",
        "No_Contact_30d": f"No Contact {no_contact_days}d+",
        "Avg_Last_Contact": "Avg Last Contact (days)",
        "Total_Touchpoints": "Contacts (90d)",
    })

    csm_summary["Total ARR"] = csm_summary["Total ARR"].map(lambda x: f"${x:,.0f}" if x else "—")
    csm_summary["Avg Last Contact (days)"] = csm_summary["Avg Last Contact (days)"].map(
        lambda x: f"{int(x)}d" if pd.notna(x) and x else "—"
    )
    csm_summary["Median Response (min)"] = csm_summary["Median Response (min)"].map(
        lambda x: f"{int(x)} min" if pd.notna(x) and x else "—"
    )
    csm_summary[f"< {response_threshold_hrs}hr"] = csm_summary[f"< {response_threshold_hrs}hr"].map(
        lambda x: f"{int(x)}%" if pd.notna(x) and x is not None else "—"
    )
    csm_summary = csm_summary.sort_values("High Risk", ascending=False)

    # Build Overall row from raw filtered data
    _all_rt = flatten_times(filtered["response_times"])
    _overall = pd.DataFrame([{
        "CSM":                      "📊 Overall",
        "Accounts":                 len(filtered),
        "High Risk":                int((filtered["risk_score"] >= 4).sum()),
        "Renewing (30d)":           int(((filtered["days_to_renewal"] >= 0) & (filtered["days_to_renewal"] <= 30)).sum()),
        "Total ARR":                f"${filtered['arr'].sum():,.0f}",
        f"No Contact {no_contact_days}d+": int((filtered["days_since_last_contact"].isna() | (filtered["days_since_last_contact"] >= no_contact_days)).sum()),
        "Avg Last Contact (days)":  f"{int(filtered['days_since_last_contact'].dropna().mean())}d" if filtered["days_since_last_contact"].notna().any() else "—",
        "Contacts (90d)":           int(filtered["total_touchpoints_90d"].sum()),
        "Median Response (min)":    f"{int(csm_median(_all_rt))} min" if _all_rt else "—",
        f"< {response_threshold_hrs}hr": f"{int(csm_pct_under(_all_rt, response_threshold))}%" if _all_rt else "—",
    }])

    st.dataframe(pd.concat([_overall, csm_summary], ignore_index=True), use_container_width=True, hide_index=True)

# ── Peer benchmark (individual CSM view) ──────────────────────────────────────
elif selected_csm != "All CSMs" or _access["role"] == "csm":
    st.divider()
    st.subheader(f"How {selected_csm.split()[0]} Compares to Team")

    def flatten_times(series):
        return [t for times in series for t in (times if isinstance(times, list) else [])]

    def _median(vals):
        if not vals:
            return None
        s = sorted(vals)
        n = len(s)
        mid = n // 2
        return round(s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2, 0)

    def _pct_under(vals, threshold):
        if not vals:
            return None
        return round(len([t for t in vals if t <= threshold]) / len(vals) * 100)

    # Compute stats for selected CSM from filtered, and team from full df
    me = filtered
    team = df_all_csms  # full team for benchmarks regardless of access level

    me_rt = flatten_times(me["response_times"])
    team_rt = flatten_times(team["response_times"])

    me_contact = me["days_since_last_contact"].dropna()
    team_contact = team["days_since_last_contact"].dropna()

    rows = [
        ("Accounts",              len(me),                                       round(len(team) / max(team["owner_name"].nunique(), 1), 1)),
        ("High Risk",             int((me["risk_score"] >= 4).sum()),            round((team["risk_score"] >= 4).sum() / max(team["owner_name"].nunique(), 1), 1)),
        (f"No Contact {no_contact_days}d+", int((me["days_since_last_contact"].isna() | (me["days_since_last_contact"] >= no_contact_days)).sum()),
                                  round((team["days_since_last_contact"].isna() | (team["days_since_last_contact"] >= 30)).sum() / max(team["owner_name"].nunique(), 1), 1)),
        ("Avg Last Contact (days)", round(me_contact.mean(), 0) if len(me_contact) else None,  round(team_contact.mean(), 0) if len(team_contact) else None),
        ("Contacts (90d)",        int(me["total_touchpoints_90d"].sum()),        round(team["total_touchpoints_90d"].sum() / max(team["owner_name"].nunique(), 1), 0)),
        ("Median Response (min)", _median(me_rt),                                _median(team_rt)),
        (f"% < {response_threshold_hrs}hr", _pct_under(me_rt, response_threshold), _pct_under(team_rt, response_threshold)),
    ]

    def fmt(val, metric):
        if val is None:
            return "—"
        if "%" in metric:
            return f"{int(val)}%"
        if "days" in metric.lower() or "response" in metric.lower():
            return f"{int(val)}"
        return str(val)

    csm_first = selected_csm.split()[0]
    bench_df = pd.DataFrame({
        "Metric":  [r[0] for r in rows],
        csm_first: [fmt(r[1], r[0]) for r in rows],
        "Team Avg": [fmt(r[2], r[0]) for r in rows],
    })

    st.dataframe(bench_df, use_container_width=False, hide_index=True)
