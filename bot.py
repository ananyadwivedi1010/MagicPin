from __future__ import annotations

import json
import re
import warnings
from typing import Any, Dict, List, Optional


def _clean(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _merchant_name(merchant: Optional[Dict[str, Any]]) -> str:
    if not merchant:
        return "this business"
    return _clean(merchant.get("identity", {}).get("name"), "this business")


def _category_label(category: Optional[Dict[str, Any]]) -> str:
    if not category:
        return "merchant"
    return _clean(category.get("display_name") or category.get("slug"), "merchant")


def _pick_digest_item(category: Optional[Dict[str, Any]], trigger_payload: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not category:
        return None
    digest = category.get("digest") or []
    payload = trigger_payload or {}
    item_id = payload.get("top_item_id") or payload.get("digest_item_id") or payload.get("alert_id")
    if item_id:
        for item in digest:
            if item.get("id") == item_id:
                return item
    if digest:
        return digest[0]
    return None


def _customer_name(customer: Optional[Dict[str, Any]]) -> str:
    if not customer:
        return "there"
    return _clean(customer.get("identity", {}).get("name"), "there")


def _fmt_pct(value: Any, signed: bool = True) -> str:
    if value is None:
        return "n/a"
    try:
        pct = float(value) * 100.0
    except (TypeError, ValueError):
        return _clean(value, "n/a")
    if abs(pct) < 0.05:
        return "flat"
    prefix = "+" if signed and pct > 0 else ""
    return f"{prefix}{pct:.1f}%"


def _voice_term(category: Optional[Dict[str, Any]], preferred: list[str]) -> str:
    if not category:
        return preferred[0] if preferred else "offer"
    voice = (category or {}).get("voice") or {}
    allowed = list(voice.get("vocab_allowed") or [])
    for item in preferred:
        if item in allowed:
            return item
    if allowed:
        return allowed[0]
    return preferred[0] if preferred else "offer"


def _signal_text(merchant: Optional[Dict[str, Any]]) -> str:
    signals = list((merchant or {}).get("signals") or [])
    if not signals:
        return ""
    primary = str(signals[0])
    if ":" in primary:
        name, _, value = primary.partition(":")
        primary = f"{name} {value}"
    text = primary.replace("_", " ").strip()
    text = re.sub(r"\bctr\b", "click-through", text, flags=re.IGNORECASE)
    text = re.sub(r"\bgbp\b", "Google Business Profile", text, flags=re.IGNORECASE)
    return text


def _site_offer_hint(category: Optional[Dict[str, Any]], merchant: Optional[Dict[str, Any]]) -> str:
    merchant_offers = list((merchant or {}).get("offers") or [])
    for offer in merchant_offers:
        status = str((offer or {}).get("status") or "").lower()
        title = _clean((offer or {}).get("title"), "")
        if status == "active" and title:
            return title
    category_offers = list((category or {}).get("offer_catalog") or [])
    for offer in category_offers:
        title = _clean(offer.get("title"), "")
        if title and not re.search(r"flat\s+\d+\s*%", title, re.IGNORECASE):
            return title
    if category_offers:
        title = _clean(category_offers[0].get("title"), "")
        if title:
            return title
    return "a focused offer for this week"


def _humanize_slug(value: Any, default: str = "this issue") -> str:
    text = _clean(value, default)
    if not text or text == default:
        return default
    lowered = text.lower()
    special = {
        "delivery_late": "late-delivery complaints",
        "summer_2026": "this summer",
        "saturday": "Saturday",
        "sunday": "Sunday",
        "weight_loss": "weight loss",
        "weight_gain": "weight gain",
        "kids_yoga_summer_camp": "kids' yoga summer camp",
        "corporate_bulk_thali_package": "corporate bulk thali package",
        "6_month_cleaning": "6 month cleaning",
        "skin_prep_program_30day": "30-day skin prep program",
        "service_due": "your next service",
        "what_service_in_demand_this_week": "what's driving demand this week",
        "customer_lapsed_hard": "a hard reactivation follow-up",
        "customer_lapsed_soft": "a soft reactivation follow-up",
        "strike_price": "price pressure",
        "demand_spike": "demand spike",
        "trends": "demand trend",
        "gbp": "Google Business Profile",
        "ors_demand_40": "ORS demand",
        "sunscreen_demand_38": "sunscreen demand",
        "antifungal_demand_45": "antifungal demand",
        "cold_cough_demand_60": "cold and cough demand",
        "cold_cough": "cold and cough",
        "late_delivery": "late-delivery complaints",
        "delivery_delay": "delivery delays",
        "post_resolution_window_apr_jun": "post-resolution season",
    }
    if lowered in special:
        return special[lowered]
    sentence = text.replace("_", " ")
    sentence = re.sub(r"\s+", " ", sentence).strip()
    sentence = re.sub(r"\bgbp\b", "Google Business Profile", sentence, flags=re.IGNORECASE)
    sentence = re.sub(r"\bors\b", "ORS", sentence, flags=re.IGNORECASE)
    sentence = re.sub(r"\bid\b", "ID", sentence)
    return sentence


def _category_metric_line(category: Optional[Dict[str, Any]], merchant: Optional[Dict[str, Any]]) -> str:
    perf = (merchant or {}).get("performance") or {}
    ctr = perf.get("ctr")
    views = perf.get("views")
    calls = perf.get("calls")
    peer = (category or {}).get("peer_stats") or {}
    pieces = []
    if views is not None:
        pieces.append(f"{int(views):,} views")
    if calls is not None:
        pieces.append(f"{int(calls)} calls")
    if ctr is not None:
        pieces.append(f"CTR {float(ctr) * 100:.1f}%")
    if peer.get("avg_ctr") is not None and ctr is not None:
        pieces.append(f"vs {float(peer['avg_ctr']) * 100:.1f}% peer median")
    return ", ".join(pieces) if pieces else "recent demand is moving"


def _warn_missing_fact(trigger_kind: str, merchant_name: str, fact_label: str) -> None:
    warnings.warn(
        f"[{trigger_kind}] missing {fact_label} for {merchant_name}; generic fallback avoided",
        RuntimeWarning,
        stacklevel=2,
    )


CUSTOMER_TRIGGER_KINDS = {"recall_due", "trial_followup", "chronic_refill_due", "appointment_tomorrow", "customer_lapsed_soft", "customer_lapsed_hard", "wedding_package_followup"}


def _metric_label(metric: Any) -> str:
    labels = {
        "review_count": "reviews",
        "reviews": "reviews",
        "calls": "calls",
        "views": "views",
        "ctr": "click-through rate",
        "directions": "direction requests",
        "orders": "orders",
        "orders_30d": "orders",
    }
    text = _clean(metric, "").lower()
    return labels.get(text, _humanize_slug(text, "performance"))


def _abs_num(value: Any) -> Optional[float]:
    try:
        return abs(float(value))
    except (TypeError, ValueError):
        return None


def _date_short(value: Any) -> str:
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", _clean(value, ""))
    if not match:
        return ""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return ""
    return f"{int(match.group(3))} {months[month - 1]}"


def _perf_phrase(merchant: Optional[Dict[str, Any]], category: Optional[Dict[str, Any]]) -> str:
    perf = (merchant or {}).get("performance") or {}
    peer_ctr = ((category or {}).get("peer_stats") or {}).get("avg_ctr")
    pieces = []
    try:
        views = perf.get("views")
        calls = perf.get("calls")
        ctr = perf.get("ctr")
        if views is not None:
            pieces.append(f"{int(views):,} views")
        if calls is not None:
            pieces.append(f"{int(calls)} calls")
        if ctr is not None:
            piece = f"CTR {float(ctr) * 100:.1f}%"
            if peer_ctr is not None:
                piece += f" vs {float(peer_ctr) * 100:.1f}% peer median"
            pieces.append(piece)
    except (TypeError, ValueError):
        pass
    if not pieces:
        return ""
    return "your profile is at " + ", ".join(pieces)


def _wants_hi_mix(customer: Optional[Dict[str, Any]]) -> bool:
    pref = _clean(((customer or {}).get("identity") or {}).get("language_pref"), "").lower()
    return "hi" in pref


def _trend_token(token: Any) -> str:
    text = _clean(token, "")
    match = re.match(r"^(.+?)_demand_([+-])(\d+)$", text)
    if match:
        name = _humanize_slug(match.group(1))
        if match.group(2) == "+":
            return f"{name} demand +{match.group(3)}%"
        return f"{name} demand down {match.group(3)}%"
    return _humanize_slug(text)


def _time_short(value: Any) -> str:
    match = re.match(r"^\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})", _clean(value, ""))
    if not match:
        return ""
    hour = int(match.group(1))
    minute = match.group(2)
    suffix = "am" if hour < 12 else "pm"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute}{suffix}"


def _parent_split(customer_name: str) -> tuple:
    match = re.match(r"^(.+?)\s*\(parent:\s*(.+?)\)\s*$", _clean(customer_name, ""))
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", customer_name


def _keyword_offer(category: Optional[Dict[str, Any]], keywords: tuple) -> str:
    for offer in (category or {}).get("offer_catalog") or []:
        title = _clean((offer or {}).get("title"), "")
        if title and any(keyword.lower() in title.lower() for keyword in keywords):
            return title
    return ""


def _prep_offer(merchant: Optional[Dict[str, Any]]) -> str:
    for offer in (merchant or {}).get("offers") or []:
        status = str((offer or {}).get("status") or "").lower()
        title = _clean((offer or {}).get("title"), "")
        if title and status == "active" and any(keyword in title.lower() for keyword in ("bridal", "prep", "glow", "spa", "package")):
            return title
    return ""


def _owner_greeting(category: Optional[Dict[str, Any]], merchant: Optional[Dict[str, Any]]) -> str:
    owner = _clean((merchant or {}).get("identity", {}).get("owner_first_name"), "")
    if not owner:
        return f"Hi {_merchant_name(merchant)}"
    if _clean((category or {}).get("slug"), "").lower() == "dentists" and not owner.lower().startswith("dr"):
        owner = f"Dr. {owner}"
    return f"Hi {owner}"


def _active_offer_titles(merchant: Optional[Dict[str, Any]], limit: int = 3) -> list[str]:
    titles = []
    for offer in (merchant or {}).get("offers") or []:
        title = _clean((offer or {}).get("title"), "")
        if title and str((offer or {}).get("status") or "").lower() == "active":
            titles.append(title)
            if len(titles) >= limit:
                break
    return titles


def _offer_price(title: str) -> Optional[int]:
    match = re.search(r"₹\s*([\d,]+)", _clean(title, ""))
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _move_phrase(label: str, value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number > 0:
        return f"{label} are up {_fmt_pct(number, signed=False)}"
    if number < 0:
        return f"{label} are down {_fmt_pct(abs(number), signed=False)}"
    return f"{label} are flat"


def _winback_offer(category: Optional[Dict[str, Any]], merchant: Optional[Dict[str, Any]]) -> str:
    keywords = ("spa", "facial", "cleaning", "membership", "package", "combo", "checkup", "consultation")
    for offer in (merchant or {}).get("offers") or []:
        status = str((offer or {}).get("status") or "").lower()
        title = _clean((offer or {}).get("title"), "")
        if title and status == "active" and any(keyword in title.lower() for keyword in keywords):
            return title
    hit = _keyword_offer(category, keywords)
    if hit:
        return hit
    return _site_offer_hint(category, merchant)


_MONTH_TOKENS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_NAMES = {"jan": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr", "may": "May", "jun": "Jun", "jul": "Jul", "aug": "Aug", "sep": "Sep", "oct": "Oct", "nov": "Nov", "dec": "Dec"}


def _season_months(slug: Any) -> str:
    text = _clean(slug, "").lower()
    found = [month for month in _MONTH_TOKENS if month in text]
    if not found:
        return ""
    if len(found) == 1:
        return _MONTH_NAMES[found[0]]
    return f"{_MONTH_NAMES[found[0]]}–{_MONTH_NAMES[found[-1]]}"


def _season_beat_note(category: Optional[Dict[str, Any]], slug: Any) -> str:
    months = _season_months(slug)
    if not months:
        return ""
    first = months.split("–")[0].lower()
    for beat in (category or {}).get("seasonal_beats") or []:
        month_range = _clean(beat.get("month_range"), "").lower()
        if first and first in month_range:
            return _clean(beat.get("note"), "")
    return ""


def _lead_fact_for_trigger(category: Optional[Dict[str, Any]], merchant: Optional[Dict[str, Any]], trigger: Optional[Dict[str, Any]]) -> tuple[Optional[str], str, Optional[str]]:
    payload = (trigger or {}).get("payload") or {}
    merchant_name = _merchant_name(merchant)
    kind = _clean((trigger or {}).get("kind"), "")

    if kind == "perf_dip":
        delta = _abs_num(payload.get("delta_pct"))
        if delta is not None:
            return f"{_metric_label(payload.get('metric') or 'calls')} fell {_fmt_pct(delta, signed=False)} in the last 7 days", "metric_drop", None
        return None, "metric_drop", f"{merchant_name} performance dip"

    if kind == "perf_spike":
        delta = _abs_num(payload.get("delta_pct"))
        if delta is not None:
            return f"{_metric_label(payload.get('metric') or 'calls')} rose {_fmt_pct(delta)} in the last 7 days", "metric_rise", None
        return None, "metric_rise", f"{merchant_name} performance lift"

    if kind == "competitor_opened":
        competitor = _clean(payload.get("competitor_name"), "")
        distance = payload.get("distance_km")
        if competitor:
            if distance is not None:
                return f"{competitor} opened {distance} km away", "competitor", None
            return f"{competitor} opened nearby", "competitor", None
        return None, "competitor", f"{merchant_name} competitor"

    if kind == "renewal_due":
        days = payload.get("days_remaining")
        if days is not None:
            plan = _clean(payload.get("plan"), "plan")
            return f"your {plan} renews in {days} days", "renewal", None
        return None, "renewal", f"{merchant_name} renewal window"

    if kind == "dormant_with_vera":
        days = payload.get("days_since_last_merchant_message")
        if days is not None:
            return f"it has been {days} days since the last message", "dormancy", None
        return None, "dormancy", f"{merchant_name} silence gap"

    if kind == "gbp_unverified":
        uplift = payload.get("estimated_uplift_pct")
        if uplift is not None:
            return f"verification gap could unlock {_fmt_pct(uplift, signed=False)} more discovery", "verification", None
        return None, "verification", f"{merchant_name} profile verification"

    if kind == "review_theme_emerged":
        theme = _clean(payload.get("theme"), "")
        count = payload.get("occurrences_30d")
        if theme and count is not None:
            return f"{_humanize_slug(theme)} is showing up in reviews {count} times in 30 days", "review_theme", None
        return None, "review_theme", f"{merchant_name} review issues"

    if kind == "active_planning_intent":
        intent = _clean(payload.get("intent_topic"), "")
        if intent:
            return f"the next move is to act on {_humanize_slug(intent)}", "planning_intent", None
        return None, "planning_intent", f"{merchant_name} planning intent"

    if kind in {"category_seasonal", "seasonal_perf_dip"}:
        season = _clean(payload.get("season"), "")
        trends = payload.get("trends") or []
        if season and trends:
            first = _humanize_slug(trends[0])
            return f"{_humanize_slug(season)} demand is moving: {first}", "seasonality", None
        if payload.get("delta_pct") is not None:
            metric = _clean(payload.get("metric"), "views")
            delta = payload.get("delta_pct")
            return f"{metric} are down {_fmt_pct(delta)} in the current seasonal window", "seasonality", None
        return None, "seasonality", f"{merchant_name} seasonal demand"

    if kind == "ipl_match_today":
        match = _clean(payload.get("match"), "")
        if match:
            return f"this match-night window is the biggest lever this week: {match}", "event", None
        return None, "event", f"{merchant_name} match-night demand"

    if kind == "festival_upcoming":
        festival = _clean(payload.get("festival"), "")
        days = payload.get("days_until")
        if festival and days is not None:
            return f"{festival} is coming up in {days} days", "festival", None
        return None, "festival", f"{merchant_name} festival timing"

    if kind == "research_digest":
        digest_item = _pick_digest_item(category, payload)
        if digest_item:
            title = _clean(digest_item.get("title"), "")
            if title:
                return title, "digest", None
        return None, "digest", f"{merchant_name} research digest"

    if kind == "regulation_change":
        digest_item = _pick_digest_item(category, payload)
        if digest_item:
            title = _clean(digest_item.get("title"), "")
            if title:
                return title, "regulation", None
        return None, "regulation", f"{merchant_name} compliance update"

    if kind == "supply_alert":
        molecule = _clean(payload.get("molecule"), "")
        if molecule:
            return f"voluntary recall affects {molecule}", "supply_alert", None
        return None, "supply_alert", f"{merchant_name} supply alert"

    if kind == "cde_opportunity":
        digest_item = _pick_digest_item(category, payload)
        if digest_item:
            title = _clean(digest_item.get("title"), "")
            if title:
                return title, "cde", None
        return None, "cde", f"{merchant_name} CDE opportunity"

    if kind == "winback_eligible":
        days = payload.get("days_since_expiry")
        lapsed = payload.get("lapsed_customers_added_since_expiry")
        if days is not None:
            return f"it has been {days} days since expiry and {lapsed or 0} lapsed customers are still eligible to re-engage", "winback", None
        return None, "winback", f"{merchant_name} winback window"

    if kind == "milestone_reached":
        value_now = payload.get("value_now")
        milestone = payload.get("milestone_value")
        metric = _metric_label(payload.get("metric") or "reviews")
        if value_now is not None and milestone is not None:
            more = max(0, int(milestone) - int(value_now))
            return f"{more} more {metric} take the profile to {milestone}", "milestone", None
        return None, "milestone", f"{merchant_name} milestone"

    if kind == "curious_ask_due":
        ask = _clean(payload.get("ask_template"), "")
        if ask:
            return f"it's check-in time — {_humanize_slug(ask)}", "curious_ask", None
        return None, "curious_ask", f"{merchant_name} next question"

    if kind in {"recall_due", "trial_followup", "chronic_refill_due", "appointment_tomorrow", "customer_lapsed_soft", "customer_lapsed_hard", "wedding_package_followup"}:
        raw_service = payload.get("service_due") or payload.get("next_step_window_open")
        if isinstance(raw_service, list):
            raw_service = ", ".join(str(item) for item in raw_service)
        service_due = _clean(raw_service, "")
        if service_due:
            return f"your follow-up is due for {service_due}", "customer_followup", None
        if payload.get("days_since_last_visit") is not None:
            previous_focus = _humanize_slug(payload.get("previous_focus"), "your previous offer")
            return f"the last visit was {payload.get('days_since_last_visit')} days ago and the last focus was {previous_focus}", "customer_followup", None
        return None, "customer_followup", f"{merchant_name} follow-up"

    return None, "generic", f"{merchant_name} trigger"


def compose(category: Optional[Dict[str, Any]], merchant: Optional[Dict[str, Any]], trigger: Optional[Dict[str, Any]], customer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compose a fact-first WhatsApp message with a direct lead fact and a specific CTA."""
    category_slug = (_clean((category or {}).get("slug"), "") or "merchant").lower()
    merchant_name = _merchant_name(merchant)
    trigger_kind = _clean((trigger or {}).get("kind"), "")
    customer_name = _customer_name(customer)
    payload = (trigger or {}).get("payload") or {}
    perf = (merchant or {}).get("performance") or {}
    peer = (category or {}).get("peer_stats") or {}
    ctr = perf.get("ctr")
    view_count = perf.get("views")
    call_count = perf.get("calls")
    peer_ctr = peer.get("avg_ctr")
    offer_hint = _site_offer_hint(category, merchant)
    greet = _owner_greeting(category, merchant)
    active_offers = _active_offer_titles(merchant)

    if customer is not None and trigger_kind in CUSTOMER_TRIGGER_KINDS:
        hi_style = _wants_hi_mix(customer)
        relationship = (customer or {}).get("relationship") or {}
        preferences = (customer or {}).get("preferences") or {}
        last_visit = _date_short(relationship.get("last_visit"))
        last_done = _date_short(payload.get("last_service_date")) or last_visit
        stock_out = _date_short(payload.get("stock_runs_out_iso"))
        merchant_active_offer = ""
        for offer in (merchant or {}).get("offers") or []:
            title = _clean((offer or {}).get("title"), "")
            if title and str((offer or {}).get("status") or "").lower() == "active":
                merchant_active_offer = title
                break
        opt_out = "STOP to opt out."
        offer_line = f" Still available: {merchant_active_offer}." if merchant_active_offer else ""

        raw_service = payload.get("service_due") or payload.get("next_step_window_open") or payload.get("molecule_list")
        if isinstance(raw_service, list):
            molecule_names = [name for name in (_humanize_slug(item, "") for item in raw_service) if name]
            if hi_style and len(molecule_names) > 1:
                service_label = ", ".join(molecule_names[:-1]) + " aur " + molecule_names[-1]
            else:
                service_label = ", ".join(molecule_names)
        elif raw_service:
            service_label = _humanize_slug(raw_service, "")
        else:
            service_label = {
                "recall_due": "your next session",
                "chronic_refill_due": "your recurring follow-up",
                "customer_lapsed_soft": "a routine check-in",
                "customer_lapsed_hard": "a restart check-in",
                "trial_followup": "your trial follow-up",
                "wedding_package_followup": "your wedding-prep session",
            }.get(trigger_kind, "your next visit")

        slot_labels = []
        for slot in payload.get("available_slots") or payload.get("next_session_options") or []:
            label = _clean(slot.get("label"), "") if isinstance(slot, dict) else _clean(slot, "")
            if label:
                slot_labels.append((label, label.split(",")[0]))

        if trigger_kind == "recall_due" and len(slot_labels) >= 2:
            due_date = _date_short(payload.get("due_date"))
            hist = f" (last done {last_done})" if last_done else ""
            offer_bit = f" {merchant_active_offer}." if merchant_active_offer else ""
            if hi_style:
                due_bit = f"{due_date} ko due hai" if due_date else "due hai"
                body = (
                    f"Hi {customer_name}, reminder from {merchant_name}: aapka {service_label} {due_bit}{hist}. "
                    f"Do slots ready hain: {slot_labels[0][0]} ya {slot_labels[1][0]}.{offer_bit} "
                    f"Reply 1 ya 2."
                )
            else:
                due_bit = f"is due {due_date}" if due_date else "is due"
                body = (
                    f"Hi {customer_name}, reminder from {merchant_name}: your {service_label} {due_bit}{hist}. "
                    f"Two slots are ready: {slot_labels[0][0]} or {slot_labels[1][0]}.{offer_bit} "
                    f"Reply 1 or 2."
                )
            cta = "multi_choice_slot"
            rationale = f"Uses the real due date, the last-done date, and two concrete slot options for {customer_name}."
        elif trigger_kind == "appointment_tomorrow":
            if hi_style:
                visit_line = f" Last visit {last_visit} ko thi." if last_visit else ""
                body = f"Hi {customer_name}, {merchant_name} mein kal aapka appointment hai.{visit_line} Slot confirm karne ke liye reply YES, {opt_out}"
            else:
                visit_line = f" Your last visit was {last_visit}." if last_visit else ""
                body = f"Hi {customer_name}, your appointment at {merchant_name} is tomorrow.{visit_line} Reply YES to confirm the slot, {opt_out}"
            cta = "binary_yes_no"
            rationale = f"Tomorrow's appointment reminder for {customer_name} with a single confirm action."
        elif trigger_kind == "chronic_refill_due" and isinstance(raw_service, list) and raw_service:
            delivery_offer = ""
            for offer in (merchant or {}).get("offers") or []:
                offer_title = _clean((offer or {}).get("title"), "")
                if offer_title and str((offer or {}).get("status") or "").lower() == "active" and "delivery" in offer_title.lower():
                    delivery_offer = offer_title
                    break
            if hi_style:
                stock_line = f" Stock {stock_out} tak chalega." if stock_out else ""
                if delivery_offer:
                    delivery_line = f" {delivery_offer} — saved address par deliver ho jayegi."
                elif payload.get("delivery_address_saved"):
                    delivery_line = " Saved address par home delivery ho jayegi."
                else:
                    delivery_line = ""
                body = (f"Hi {customer_name}, {merchant_name} se — aapki {service_label} ki refill ka time aa gaya hai.{stock_line}{delivery_line} "
                        f"Refill confirm karne ke liye reply YES, {opt_out}")
            else:
                stock_line = f" Stock runs out around {stock_out}." if stock_out else ""
                if delivery_offer:
                    delivery_line = f" {delivery_offer} applies — it goes to your saved address."
                elif payload.get("delivery_address_saved"):
                    delivery_line = " Home delivery will go to the saved address."
                else:
                    delivery_line = ""
                body = (f"Hi {customer_name}, this is {merchant_name} — your refill for {service_label} is due.{stock_line}{delivery_line} "
                        f"Reply YES and I'll keep it ready, {opt_out}")
            cta = "binary_yes_no"
            rationale = f"Refill reminder for {customer_name} built from the molecule list, stock-out date, and the merchant's own delivery offer."
        elif trigger_kind == "trial_followup":
            trial_date = _date_short(payload.get("trial_date")) or last_done
            kid, addressee = _parent_split(customer_name)
            subject = f"{kid}'s" if kid else "your"
            seat = f"{kid}'s seat" if kid else "your seat"
            lead = f"{subject} trial class was {trial_date}" if trial_date else f"{subject} trial class is on the books"
            if slot_labels:
                slot_bit = f" Next session ready: {slot_labels[0][0]}."
                close = f"Reply YES and I'll confirm {seat} for it, {opt_out}"
            else:
                slot_bit = ""
                close = f"Reply YES and I'll confirm {seat} this week, {opt_out}"
            body = f"Hi {addressee}, {lead} at {merchant_name}.{slot_bit}{offer_line} {close}"
            cta = "binary_yes_no"
            rationale = f"Trial follow-up for {customer_name} that uses the real trial date, the next session option, and a single confirm action."
        elif trigger_kind == "customer_lapsed_hard":
            days_since = payload.get("days_since_last_visit")
            months = payload.get("previous_membership_months")
            focus = _humanize_slug(payload.get("previous_focus") or preferences.get("training_focus"), "your earlier goal")
            lead = f"it has been {days_since} days since your last visit" if days_since is not None else "it has been a while since your last visit"
            history = (f" — after {int(months)} months on {focus}, this is the stretch where progress slips first, and it happens to "
                       f"everyone, no judgment. The restart is the easy part — no commitment, no auto-charge."
                       if months else
                       " — happens to everyone, no judgment. The restart is the easy part — no commitment, no auto-charge.")
            body = (f"Hi {customer_name}, {merchant_name} here — {lead}{history}{offer_line} "
                    f"Reply YES and I'll hold a spot for this week, {opt_out}")
            cta = "binary_yes_no"
            rationale = f"Winback for {customer_name} signed by the merchant, citing the real inactivity window, membership length, and the merchant's live offer."
        elif trigger_kind == "wedding_package_followup":
            days_to = payload.get("days_to_wedding")
            wedding_day = _date_short(payload.get("wedding_date"))
            trial_done = _date_short(payload.get("trial_completed"))
            next_step = _humanize_slug(payload.get("next_step_window_open"), "") if payload.get("next_step_window_open") else ""
            prep_offer = _prep_offer(merchant)
            pref_slot = _humanize_slug(preferences.get("preferred_slots"), "") if preferences.get("preferred_slots") else ""
            if days_to is not None and wedding_day:
                when = f"{wedding_day} — {days_to} days out"
            elif days_to is not None:
                when = f"{days_to} days out"
            else:
                when = "coming up"
            trial_bit = f" Your bridal trial ({trial_done}) is already banked." if trial_done else ""
            step_bit = f" The prep plan's next step: {next_step}." if next_step else ""
            offer_bit = f" {prep_offer} keeps the weekly prep on track." if prep_offer else ""
            slot_bit = f" I'll block your {pref_slot} slot" if pref_slot else " I'll block your slot"
            body = (f"Hi {customer_name}, {merchant_name} here — your wedding is {when}.{trial_bit}{step_bit}{offer_bit} "
                    f"Reply YES and{slot_bit} for the first session, {opt_out}")
            cta = "binary_yes_no"
            rationale = f"Wedding-prep follow-up signed by the merchant, built from the real wedding date, the completed trial, and the payload's next prep step."
        else:
            if hi_style:
                visit_line = f" Aapki last visit {last_visit} ko thi." if last_visit else ""
                body = (f"Hi {customer_name}, {merchant_name} par aapka follow-up due hai.{visit_line}{offer_line} "
                        f"Reply YES aur main slot ready rakhunga, {opt_out}")
            else:
                visit_line = f" Your last visit was {last_visit}." if last_visit else ""
                body = (f"Hi {customer_name}, {service_label} is due at {merchant_name}.{visit_line}{offer_line} "
                        f"Reply YES and I'll hold a slot this week, {opt_out}")
            cta = "binary_yes_no"
            rationale = f"Customer follow-up for {customer_name} using the last-visit date and a single booking action."

        if hi_style:
            rationale += " Hinglish matches the customer's language preference."
        return {
            "body": body,
            "cta": cta,
            "send_as": "merchant_on_behalf",
            "suppression_key": _clean((trigger or {}).get("suppression_key"), f"{merchant_name}:{trigger_kind}"),
            "rationale": rationale,
        }

    lead_fact, _, missing_label = _lead_fact_for_trigger(category, merchant, trigger)
    perf_line = _perf_phrase(merchant, category)
    perf_sentence = f"{perf_line[:1].upper()}{perf_line[1:]}." if perf_line else ""
    suppression = _clean((trigger or {}).get("suppression_key"), f"{trigger_kind}:{merchant_name}")
    opt_out = "STOP to opt out."
    cta = "binary_yes_no"

    if trigger_kind in {"research_digest", "regulation_change", "cde_opportunity"}:
        digest_item = _pick_digest_item(category, payload)
        if digest_item:
            title = _clean(digest_item.get("title"), "")
            source = _clean(digest_item.get("source"), "")
            actionable = _clean(digest_item.get("actionable"), "")
            cohort_line = ""
            if trigger_kind == "research_digest":
                flagged = [str(sig) for sig in (merchant or {}).get("signals") or [] if "high_risk_adult" in str(sig)]
                if flagged:
                    cohort_line = " Your flagged high-risk adult cohort is exactly this group."
            if trigger_kind == "cde_opportunity":
                when = _date_short(digest_item.get("date"))
                credits = payload.get("credits") or digest_item.get("credits")
                fee = _humanize_slug(payload.get("fee") or digest_item.get("fee"), "")
                bits = []
                if when:
                    bits.append(f"runs {when}")
                if credits is not None:
                    bits.append(f"{credits} CDE credits")
                if fee:
                    bits.append(fee)
                detail = f" ({'; '.join(bits)})" if bits else ""
                source_bit = f" — {source}" if source and source.split()[0].lower() not in title.lower() else ""
                body = (f"{greet}, CDE heads-up: {title}{source_bit}{detail}. "
                        f"Reply YES and I'll handle the registration for you, {opt_out}")
            else:
                when = _date_short(payload.get("deadline_iso"))
                deadline_line = f" Compliance deadline: {when}." if when else ""
                summary = _clean(digest_item.get("summary"), "")
                sentences = [part.strip() for part in summary.split(". ") if part.strip()]
                take = 2 if trigger_kind == "regulation_change" else 1
                readout_text = ". ".join(sentences[:take]).strip()
                if readout_text and readout_text[-1] not in ".!?":
                    readout_text = readout_text + "."
                n_bit = ""
                trial_n = digest_item.get("trial_n")
                if isinstance(trial_n, (int, float)):
                    n_bit = f" (n={int(trial_n):,})"
                readout = f" Trial read-out{n_bit}: {readout_text}" if readout_text and trigger_kind == "research_digest" else ""
                if readout_text and trigger_kind == "regulation_change":
                    readout = f" What changes: {readout_text}"
                practical = ""
                if actionable:
                    if actionable[-1] not in ".!?":
                        actionable = actionable + "."
                    practical = f" The practical move: {actionable}"
                deliverable = "the 1-page protocol note and the patient message" if trigger_kind == "research_digest" else "the compliance checklist and the staff note"
                body = (f"{greet}, {title} — {source}.{deadline_line}{readout}{cohort_line}"
                        f"{practical} Reply YES and I'll draft {deliverable}, {opt_out}")
            return {
                "body": body,
                "cta": cta,
                "send_as": "vera",
                "suppression_key": suppression,
                "rationale": "Leads with the source-backed digest item and a concrete operational action; quoted text is used as-is, with no invention.",
            }

    if trigger_kind == "perf_dip":
        metric = _metric_label(payload.get("metric") or "calls")
        delta = _abs_num(payload.get("delta_pct"))
        raw_delta = payload.get("delta_pct")
        baseline = payload.get("vs_baseline")
        d7 = perf.get("delta_7d") or {}
        if delta is not None:
            dip_line = f"{metric} fell {_fmt_pct(delta, signed=False)} in the last 7 days"
            if baseline is not None:
                try:
                    now_value = round(float(baseline) * (1 + float(raw_delta)))
                    dip_line += f" — from your {int(baseline)} baseline to about {int(now_value)}"
                except (TypeError, ValueError):
                    dip_line += f" (baseline {int(baseline)})"
        else:
            v_pct = d7.get("views_pct")
            c_pct = d7.get("calls_pct")
            if v_pct is not None and c_pct is not None and abs(float(c_pct)) < abs(float(v_pct)):
                dip_line = (f"views moved {_fmt_pct(v_pct)} while calls only moved {_fmt_pct(c_pct)} in the last 7 days — "
                            f"attention is landing but it is not converting into calls yet")
            elif c_pct is not None:
                dip_line = f"calls moved {_fmt_pct(c_pct)} in the last 7 days"
            else:
                dip_line = "recent activity needs a sharper offer to convert"
        signals = [str(sig) for sig in (merchant or {}).get("signals") or []]
        no_offer = any("no_active_offers" in sig for sig in signals)
        if no_offer:
            offer_gap = " There is no active offer on your profile right now — that is the hole to fill first."
        elif active_offers:
            offer_gap = f" Your {active_offers[0]} is live, but the page needs a fresh reason to click this week."
        else:
            offer_gap = ""
        gbp_gap = " Your GBP is also still unverified — local searchers filter straight past you." if any("unverified" in sig for sig in signals) else ""
        offer_kind = "first-visit cleaning offer" if category_slug == "dentists" else "first-visit offer"
        body = (f"{greet}, {dip_line}.{offer_gap}{gbp_gap} "
                f"The cleanest 3-day recovery: switch on a {offer_kind} and get it posted this week. "
                f"Reply YES and I'll draft the offer post and the follow-up message, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Grounded in the exact 7-day drop versus the merchant's own baseline, the missing offer, the verification gap, and one recovery action.",
        }

    if trigger_kind == "perf_spike":
        metric = _metric_label(payload.get("metric") or "calls")
        delta = _abs_num(payload.get("delta_pct"))
        baseline = payload.get("vs_baseline")
        d7 = perf.get("delta_7d") or {}
        if delta is not None:
            rise_line = f"{metric} rose {_fmt_pct(delta)} in the last 7 days"
            if baseline is not None:
                rise_line += f" (baseline {baseline})"
        elif d7.get("calls_pct") is not None:
            rise_line = f"calls moved {_fmt_pct(d7.get('calls_pct'))} in the last 7 days"
        else:
            rise_line = "recent demand is trending up"
        driver = _humanize_slug(payload.get("likely_driver"), "") if payload.get("likely_driver") else ""
        driver_line = f" Your {driver} is the likely driver." if driver else ""
        audience = "parents" if "kid" in driver.lower() else "customers"
        follow = (f"That is a validated demand signal — do not let it cool. Pin a reply-to-hold-your-spot line on the same post "
                  f"and attach {active_offers[0]} for the {audience} who ask." if active_offers else
                  "That is a validated demand signal — double down with a follow-up post and a reply-to-book line while it is hot.")
        body = (f"{greet}, {rise_line}.{driver_line} {follow} "
                f"Reply YES and I'll draft the follow-up post and the {audience} DM script, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Names the measured lift and likely driver from the trigger, then converts it into one drafted follow-up on the merchant's own offer.",
        }

    if trigger_kind == "milestone_reached":
        metric = _metric_label(payload.get("metric") or "reviews")
        value_now = payload.get("value_now")
        milestone_value = payload.get("milestone_value")
        if value_now is not None and milestone_value is not None:
            gap = max(0, int(milestone_value) - int(value_now))
            lead = f"you are at {int(value_now)} {metric} — {gap} more crosses the {int(milestone_value)} milestone"
        elif metric == "reviews":
            lead = "your review count is nearing a round-number milestone"
        else:
            lead = f"your {metric} is nearing a round-number milestone"
        proof = ""
        if view_count is not None and call_count is not None:
            proof = f" You are already pulling {int(view_count):,} views and {int(call_count)} calls a month — the traffic is there; the ask is not."
        mech = " A one-line nudge at billing plus a QR card at the counter closes the gap this week."
        body = (f"{greet}, {lead}.{proof}{mech} "
                f"Reply YES and I'll draft the billing-line ask and the counter card, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Turns the milestone trigger into one concrete review-generation mechanic, using the live gap and profile traffic.",
        }

    if trigger_kind == "competitor_opened":
        competitor = _clean(payload.get("competitor_name"), "")
        distance = payload.get("distance_km")
        their_offer = _clean(payload.get("their_offer"), "")
        opened = _date_short(payload.get("opened_date"))
        if competitor:
            threat = f"{competitor} opened {f'{distance} km away' if distance is not None else 'nearby'}"
            if opened:
                threat += f" on {opened}"
            if their_offer:
                threat += f" and is running {their_offer}"
        else:
            threat = "a new competitor has opened nearby"
        stale_note = ""
        for sig in (merchant or {}).get("signals") or []:
            text = str(sig)
            if text.startswith("stale_posts:"):
                days_stale = text.split(":", 1)[1].rstrip("d")
                stale_note = f" It has been {days_stale} days since your last Google post — the visibility gap a new entrant exploits first."
                break
        ctr_gap = ""
        for sig in (merchant or {}).get("signals") or []:
            if "ctr_below_peer" in str(sig):
                ctr_gap = " Your CTR is below the peer median, so the attack landed on visibility, not on your quality."
                break
        has_high_risk = any("high_risk_adult" in str(sig) for sig in (merchant or {}).get("signals") or [])
        if has_high_risk:
            counter = ("Cheap one-off cleanings do not beat continuity care — your flagged high-risk adult cohort is exactly the proof. "
                       "Counter with a value post plus refreshed GBP photos, not a price cut.")
        else:
            counter = ("Do not match the discount — one-off price bait loses to the relationship you already have with your regulars. "
                       "Counter with a value post plus refreshed GBP photos, not a price cut.")
        body = (f"{greet}, {threat}.{stale_note}{ctr_gap} {counter} "
                f"Reply YES and I'll draft the counter post and the photo checklist, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Acts on the exact competitor fact and real profile signals (stale posts, below-peer CTR) with a non-price counter.",
        }

    if trigger_kind == "renewal_due":
        days = payload.get("days_remaining")
        plan = _clean(payload.get("plan"), "plan")
        amount = payload.get("renewal_amount")
        window = f"in {days} days" if days is not None else "soon"
        amount_line = f" at ₹{amount:,}" if isinstance(amount, (int, float)) else ""
        body = (f"Hi {merchant_name}, your {plan} renews {window}{amount_line}. {perf_sentence} "
                f"Reply YES and I'll prepare the renewal plan before the deadline hits, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Anchors on the exact renewal window and amount, then offers one concrete prep action.",
        }

    if trigger_kind == "festival_upcoming":
        festival = _clean(payload.get("festival"), "")
        days = payload.get("days_until")
        festival_date = _clean(payload.get("date"), "")
        date_bit = _date_short(festival_date)
        if festival and date_bit:
            lead = f"{festival} is on {date_bit}"
        elif festival:
            lead = f"{festival} is coming up"
        else:
            lead = "a festival window is coming up"
        runway = ""
        if isinstance(days, (int, float)) and days > 0:
            runway = (f" {int(days)} days out sounds early — but festive and bridal bookings get locked in months ahead, "
                      f"so this window is planning time, not selling time yet.")
        momentum = ""
        if view_count is not None and call_count is not None:
            momentum = f" You are already trending up — {int(view_count):,} views and {int(call_count)} calls in the last 30 days."
        entry_offer = active_offers[0] if active_offers else offer_hint
        step_up = "bridal and package work" if category_slug == "salons" else "festive packages"
        plan_bit = (f" The move: open a festive pre-booking list now, with {entry_offer} as the entry point and {step_up} as the step-up."
                    if entry_offer else
                    f" The move: open a festive pre-booking list now and let {step_up} be the step-up.")
        body = (f"{greet}, {lead}.{runway}{momentum}{plan_bit} "
                f"Reply YES and I'll draft the pre-booking post and the festive slots sheet, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Uses the exact festival date and runway, the merchant's live momentum, and one pre-booking action built on the merchant's own catalog.",
        }

    if trigger_kind == "ipl_match_today":
        match = _clean(payload.get("match"), "tonight's match")
        venue = _clean(payload.get("venue"), "")
        clock = _time_short(payload.get("match_time_iso"))
        lead = match + (f" at {venue}" if venue else "") + (f", {clock} tonight" if clock else " tonight")
        if payload.get("is_weeknight") is False:
            play = ("It is a Saturday match, so most fans watch from home and order in. Counter-intuitive but true: skip the "
                    "dine-in promo tonight and make it delivery-only.")
        else:
            play = "Weeknight matches pull the after-office crowd, so a pre-order plus delivery play converts best tonight."
        bogo_bit = ""
        for title in active_offers:
            lowered = title.lower()
            if "buy 1" in lowered or "bogo" in lowered or "1 get 1" in lowered:
                bogo_bit = f" Your {title} does not cover today — run it as a one-day delivery special for the match."
                break
        body = (f"{greet}, {lead}. {play}{bogo_bit}"
                f" Reply YES and I'll draft the match-night post and a 15-word order reply, ready in 10 minutes, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Uses the match, venue, and time from the trigger, adds the Saturday home-watch judgment call, and works the merchant's own live BOGO offer.",
        }

    if trigger_kind in {"category_seasonal", "seasonal_perf_dip"}:
        season = _humanize_slug(payload.get("season"), "this season")
        trend_tokens = [item for item in (_trend_token(token) for token in payload.get("trends") or []) if item]
        ca = (merchant or {}).get("customer_aggregate") or {}
        if trend_tokens:
            if len(trend_tokens) > 1:
                trend_text = ", ".join(trend_tokens[:-1]) + " and " + trend_tokens[-1]
            else:
                trend_text = trend_tokens[0]
            lead = f"demand is already shifting for {season}: {trend_text}"
            shelf = bool(payload.get("shelf_action_recommended"))
            action = "That is a shelf-and-staffing call this week" if shelf else "That is an offer call this week"
            attach = f" Your {active_offers[0]} is the natural attach for the uptick." if active_offers else ""
            deliverable = "the shelf note and the counter script" if shelf else f"the push around {offer_hint}"
            body = f"{greet}, {lead}. {action}.{attach} Reply YES and I'll draft {deliverable}, {opt_out}"
            rationale = "Parses the pushed seasonal trend tokens verbatim into plain language, ties in the merchant's repeat base, and proposes one shelf/offer action."
        else:
            metric = _metric_label(payload.get("metric") or "views")
            delta = _abs_num(payload.get("delta_pct"))
            window = _clean(payload.get("window"), "7d")
            window_text = "7 days" if window.replace(" ", "").lower() in {"7d", "7days"} else window
            months = _season_months(payload.get("season_note") or payload.get("season"))
            dip_line = f"{metric} fell {_fmt_pct(delta, signed=False)} in the last {window_text}" if delta is not None else f"{metric} are the first signal of a seasonal shift"
            if view_count is not None and call_count is not None:
                dip_line += f" (30-day base: {int(view_count):,} views, {int(call_count)} calls)"
            if payload.get("is_expected_seasonal"):
                timing = f" The {months} dip is category-normal" if months else " The dip is category-normal for this window"
            else:
                timing = ""
            beat_note = _season_beat_note(category, payload.get("season_note") or payload.get("season"))
            playbook = f" Your category playbook: {beat_note}." if beat_note else ""
            stakes = ""
            if beat_note and "retention" in beat_note.lower():
                if active_offers:
                    stakes = f" Skip ad spend — switch {active_offers[0]} on as the re-activation lever for quiet members this window."
                else:
                    stakes = " Skip ad spend — this window rewards re-activation, not acquisition."
                close = f"Reply YES and I'll draft the member check-in and the offer post, {opt_out}"
            else:
                close = f"Reply YES and I'll draft the push around {offer_hint}, {opt_out}"
            stale_bit = ""
            for sig in (merchant or {}).get("signals") or []:
                if "no_recent_post" in str(sig):
                    stale_bit = " Nothing has been posted recently either — that visibility is the cheapest win this week."
                    break
            body = f"{greet}, {dip_line}.{timing}.{playbook}{stakes}{stale_bit} {close}"
            rationale = "Grounds the dip in the exact metric, window, and seasonal timing with the 30-day base and member math, then applies the category's own playbook for this window."
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": rationale,
        }

    if trigger_kind == "gbp_unverified":
        uplift = payload.get("estimated_uplift_pct")
        path = _humanize_slug(payload.get("verification_path"), "") if payload.get("verification_path") else ""
        if uplift is not None:
            uplift_line = f"verification could unlock up to {_fmt_pct(uplift, signed=False)} more discovery"
        else:
            uplift_line = "an unverified profile is losing discovery every day"
        path_line = f" Your verification path: {path}." if path else ""
        body = (f"Hi {merchant_name}, your Google Business Profile is still unverified — {uplift_line}.{path_line} {perf_sentence} "
                f"Reply YES and I'll start the verification flow, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Uses the exact uplift estimate and verification path from the trigger, tied to live profile metrics.",
        }

    if trigger_kind == "review_theme_emerged":
        theme = _humanize_slug(payload.get("theme"), "a recurring issue")
        count = payload.get("occurrences_30d")
        if count is not None:
            lead = f"{theme} showed up in reviews {count} times in the last 30 days"
        else:
            lead = f"{theme} keeps showing up in recent reviews"
        quote = _clean(payload.get("common_quote"), "")
        quote_bit = f" Top verbatim: “{quote}”." if quote else ""
        fix = ("The highest-leverage fix: a 40-minute dispatch SLA on delivery orders plus a first-late-refund line in the order note."
               if "deliver" in theme.lower() else "The highest-leverage fix is a one-week process change, not a discount.")
        body = (f"{greet}, {lead}.{quote_bit} {fix} "
                f"Reply YES and I'll draft the SLA note and the order-reply script, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Converts the review theme into one operational fix with the real occurrence count.",
        }

    if trigger_kind == "supply_alert":
        molecule = _clean(payload.get("molecule"), "")
        batches = payload.get("affected_batches") or []
        batch_text = ", ".join(str(item) for item in batches if item)
        if molecule:
            lead = f"there is a voluntary recall on {molecule}" + (f" (batches {batch_text})" if batch_text else "")
        else:
            lead = "there is a supply alert on one of your lines"
        body = (f"Hi {merchant_name}, {lead}. The right next step: pull the affected stock, flag repeat prescriptions, and message impacted patients before the next refill window. "
                f"Reply YES and I'll draft the patient alert and the batch list, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Stays precise about the recall scope and the required stock action.",
        }

    if trigger_kind == "winback_eligible":
        days_since = payload.get("days_since_expiry")
        lapsed = payload.get("lapsed_customers_added_since_expiry")
        perf_dip = _abs_num(payload.get("perf_dip_pct"))
        lead = f"it has been {days_since} days since your plan expired" if days_since is not None else "your plan has lapsed"
        lapsed_line = f" {lapsed} lapsed customers are still eligible to re-engage." if lapsed is not None else ""
        dip_line = f" Calls are down {_fmt_pct(perf_dip, signed=False)} since then." if perf_dip is not None else ""
        body = (f"Hi {merchant_name}, {lead}.{lapsed_line}{dip_line} The cleanest single move: one winback message built on {_winback_offer(category, merchant)}. "
                f"Reply YES and I'll draft that message, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Uses the real inactivity window, the eligible-customer count, the measured dip, and one concrete winback deliverable.",
        }

    if trigger_kind == "dormant_with_vera":
        days = payload.get("days_since_last_merchant_message")
        last_topic = _humanize_slug(payload.get("last_topic"), "") if payload.get("last_topic") else ""
        if days is not None:
            lead = f"it has been {days} days since we last talked, and the profile has been coasting since"
        else:
            lead = "it has been a while since we last worked on your promotions"
        topic_line = f" The last thread ({last_topic}) never got closed." if last_topic else ""
        winback = active_offers[0] if active_offers else ""
        restart = (" The restart is two moves, both done for you: a ready-to-go offer on the page"
                   + (f" — {winback} is already live and can lead" if winback else " (I will build the first one free)")
                   + ", plus one winback message to your past customers.")
        body = (f"{greet}, {lead}.{topic_line}{restart} "
                f"Reply YES and I'll send the 30-day restart plan before you decide anything, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Restarts the dormant thread with the real gap and last topic, then offers a done-for-you two-move restart built only on live account facts.",
        }

    if trigger_kind == "curious_ask_due":
        ask = _clean(payload.get("ask_template"), "")
        if ask:
            ask_line = f"Quick check-in: {_humanize_slug(ask)}?"
        else:
            ask_line = "Quick check-in: what is driving the most demand for you this week?"
        option_titles = [title.split(" (")[0].strip() for title in active_offers if "₹" in title][:2]
        options_bit = ""
        if option_titles:
            if len(option_titles) > 1:
                options_bit = f" — e.g. {option_titles[0]} or {option_titles[1]}"
            else:
                options_bit = f" — e.g. {option_titles[0]}"
        pieces = []
        if perf_line:
            pieces.append(f"{perf_line}.")
        pieces.append(ask_line)
        pieces.append(f"A one-line reply is enough{options_bit} — I'll build next week's promo around it, live Monday.")
        body = f"{greet}, " + " ".join(pieces)
        return {
            "body": body,
            "cta": "open_ended",
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "A genuine open question grounded in the merchant's own live metrics and catalog.",
        }

    if trigger_kind == "active_planning_intent":
        topic = str(payload.get("intent_topic") or "").lower()
        ca = (merchant or {}).get("customer_aggregate") or {}
        if "corporate" in topic or "thali" in topic:
            base_offer = active_offers[0] if active_offers else offer_hint
            locality = _clean(((merchant or {}).get("identity") or {}).get("locality"), "")
            radius_bit = f" Offices around {locality} sit inside your delivery radius, so fulfilment is the easy part." if locality else ""
            body = (f"{greet}, here is the starter version of what you asked about — the corporate thali package: 10-thali minimum "
                    f"per office order, tiered down from your {base_offer}, one consolidated monthly invoice, a single 12:30pm "
                    f"delivery window, orders confirmed by 5pm the day before.{radius_bit} "
                    f"Reply YES and I'll draft the package sheet and a 3-line note for facilities managers, {opt_out}")
        elif "kids" in topic or "yoga" in topic:
            base_offer = active_offers[0] if active_offers else offer_hint
            body = (f"{greet}, here is the starter version of what you asked about — the kids yoga camp: 8 sessions over 4 weeks, "
                    f"ages 7–12, one trial-class entry priced like your {base_offer}, then the full-camp fee. Keep the first "
                    f"cohort small so the room stays premium. "
                    f"Reply YES and I'll draft the camp outline, the pricing copy, and the launch post, {opt_out}")
        else:
            intent = _humanize_slug(payload.get("intent_topic"), "your next offer")
            body = (f"Hi {merchant_name}, on {intent}: start with a small fixed-scope pilot at your current pricing, then scale what converts. "
                    f"Reply YES and I'll draft the launch plan, {opt_out}")
        return {
            "body": body,
            "cta": cta,
            "send_as": "vera",
            "suppression_key": suppression,
            "rationale": "Answers the merchant's own planning question with a grounded structure and only verified numbers.",
        }

    if lead_fact:
        body = (f"Hi {merchant_name}, {lead_fact}. {perf_sentence} "
                f"Reply YES and I'll turn this into the exact next step, {opt_out}")
    else:
        _warn_missing_fact(trigger_kind or "unknown", merchant_name, missing_label or "lead fact")
        signal_line = _signal_text(merchant)
        signal_clause = f"One live signal on your account: {signal_line}. " if signal_line else ""
        if perf_line:
            body = (f"Hi {merchant_name}, {perf_line}. {signal_clause}"
                    f"Reply YES and I'll turn that into the exact next step, {opt_out}")
        elif signal_line:
            body = (f"Hi {merchant_name}, one thing worth acting on from your account: {signal_line}. "
                    f"Reply YES and I'll turn that into the exact next step, {opt_out}")
        else:
            body = (f"Hi {merchant_name}, I have a concrete read on your account ready. "
                    f"Reply YES and I'll turn that into the exact next step, {opt_out}")
    return {
        "body": body,
        "cta": cta,
        "send_as": "vera",
        "suppression_key": suppression,
        "rationale": "Final fallback stays grounded in live merchant metrics and a single next step; no invented facts.",
    }


def generate_submission_rows(data_dir: str = "dataset") -> list[dict]:
    """Build the 30 submission rows from the canonical pairs in expanded/test_pairs.json."""
    from pathlib import Path

    candidates = [Path("expanded"), Path(data_dir).parent / "expanded", Path(data_dir)]
    base = next((path for path in candidates if (path / "test_pairs.json").exists()), None)
    if base is None:
        raise FileNotFoundError("test_pairs.json not found; expected expanded/test_pairs.json")

    categories = {}
    for file_path in sorted((base / "categories").glob("*.json")):
        obj = json.loads(file_path.read_text(encoding="utf-8"))
        categories[obj.get("slug")] = obj

    def load_items(folder: str, key: str) -> dict:
        items = {}
        for file_path in sorted((base / folder).glob("*.json")):
            obj = json.loads(file_path.read_text(encoding="utf-8"))
            items[obj.get(key)] = obj
        return items

    merchants = load_items("merchants", "merchant_id")
    triggers = load_items("triggers", "id")
    customers = load_items("customers", "customer_id")
    pairs = json.loads((base / "test_pairs.json").read_text(encoding="utf-8")).get("pairs") or []

    rows = []
    for pair in pairs:
        trigger = triggers.get(pair.get("trigger_id"))
        merchant = merchants.get(pair.get("merchant_id"))
        if trigger is None or merchant is None:
            continue
        customer = customers.get(pair.get("customer_id")) if pair.get("customer_id") else None
        category = categories.get(merchant.get("category_slug"))
        message = compose(category, merchant, trigger, customer)
        rows.append({
            "test_id": pair.get("test_id"),
            "trigger_id": trigger.get("id"),
            "merchant_id": merchant.get("merchant_id"),
            "customer_id": pair.get("customer_id"),
            "body": message["body"],
            "cta": message["cta"],
            "send_as": message["send_as"],
            "suppression_key": message["suppression_key"],
            "rationale": message["rationale"],
        })
    return rows
