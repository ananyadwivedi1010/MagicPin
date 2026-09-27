from __future__ import annotations

import json
import re
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot import CUSTOMER_TRIGGER_KINDS, compose


class ContextPayload(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: Optional[str] = None


class TickPayload(BaseModel):
    now: Optional[str] = None
    available_triggers: List[str] = []


class ReplyPayload(BaseModel):
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str = ""
    received_at: Optional[str] = None
    turn_number: int = 1


class ActionResponse(BaseModel):
    action: str
    body: Optional[str] = None
    cta: Optional[str] = None
    rationale: Optional[str] = None
    wait_seconds: Optional[int] = None


app = FastAPI(title="magicpin Vera Bot", version="1.0.0")


def reset_state() -> None:
    state.clear()
    state.update({
        "categories": {},
        "merchants": {},
        "customers": {},
        "triggers": {},
        "context_versions": {},
        "sent_suppressions": set(),
        "conversation_store": {},
        "started_at": time.time(),
    })


state = {}
reset_state()

AUTO_REPLY_PATTERNS = [
    r"thank you for contacting", r"we will respond shortly", r"thank you for your message",
    r"our team will get back", r"we are closed", r"busy right now", r"auto[- ]?reply",
    r"this is an automated", r"we will reply", r"will get back to you soon",
]
STOP_PATTERNS = [
    r"\bstop\b", r"\bunsubscribe\b", r"\bdo not message\b", r"\bnot interested\b",
    r"\bno more messages\b", r"\bspam\b", r"\bopt out\b",
]
YES_PATTERNS = [
    r"\byes\b", r"\byep\b", r"\bsure\b", r"\bok\b", r"\bokay\b", r"\blet'?s do it\b",
    r"\bplease do\b", r"\bsend it\b", r"\bgo ahead\b", r"\bsounds good\b", r"\bdo it\b",
    r"\bhaan\b", r"\bkar do\b", r"\bchalo\b",
]
WAIT_PATTERNS = [r"\bnot now\b", r"\blater\b", r"\bbusy\b", r"\bcan do later\b", r"\bneed time\b", r"\bcall me later\b"]


def matches_patterns(text: str, patterns: List[str]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_response(**kwargs):
    return {k: v for k, v in kwargs.items() if v is not None}


@app.get("/v1/healthz")
def healthz():
    runtime = int(time.time() - state["started_at"])
    return {
        "status": "ok",
        "uptime_seconds": runtime,
        "contexts_loaded": {
            "category": len(state["categories"]),
            "merchant": len(state["merchants"]),
            "customer": len(state["customers"]),
            "trigger": len(state["triggers"]),
        },
    }


@app.get("/v1/metadata")
def metadata():
    return {
        "team_name": "MagicPin Vera Bot",
        "team_members": ["Ananya", "AI Engineer"],
        "model": "rule-based-deterministic-composer",
        "approach": "Context-aware deterministic rules with retrieval from category, merchant, trigger, and customer data",
        "contact_email": "team@example.com",
        "version": "1.0.0",
        "submitted_at": utc_now().isoformat(),
    }


@app.post("/v1/admin/reset")
def admin_reset():
    reset_state()
    return {"status": "ok", "message": "in-memory state reset"}


@app.post("/v1/teardown")
def teardown():
    reset_state()
    return {"status": "ok", "message": "state wiped"}


@app.post("/v1/context")
def push_context(ctx: ContextPayload):
    scope = ctx.scope
    if scope not in {"category", "merchant", "customer", "trigger"}:
        return JSONResponse(
            status_code=400,
            content={"accepted": False, "reason": "invalid_scope", "details": f"Unsupported scope: {scope}"},
        )

    key = (scope, ctx.context_id)
    current_version = state["context_versions"].get(key)
    if current_version is not None and ctx.version <= current_version:
        return JSONResponse(
            status_code=409,
            content={"accepted": False, "reason": "stale_version", "current_version": current_version},
        )

    state["context_versions"][key] = ctx.version
    if scope == "category":
        state["categories"][ctx.context_id] = ctx.payload
    elif scope == "merchant":
        state["merchants"][ctx.context_id] = ctx.payload
    elif scope == "customer":
        state["customers"][ctx.context_id] = ctx.payload
    else:
        state["triggers"][ctx.context_id] = ctx.payload

    return {"accepted": True, "ack_id": f"ack_{uuid.uuid4().hex[:8]}", "stored_at": utc_now().isoformat()}


# challenge-testing-brief.md §5: at most 20 actions per tick.
MAX_ACTIONS_PER_TICK = 20


@app.post("/v1/tick")
def tick(payload: TickPayload):
    actions = []
    available = payload.available_triggers or []

    def is_invalid_body(body: Optional[str]) -> bool:
        if not body:
            return True
        normalized = body.lower()
        if "i don't have enough data" in normalized or "insufficient data" in normalized or "no data" in normalized:
            return True
        if "the clearest move for this business" in normalized or "lean into" in normalized:
            return True
        if re.search(r"(?<![a-z0-9])[a-z]+_[a-z0-9_]+", normalized):
            return True
        return False

    print(f"[tick] processing {len(available)} trigger(s): {available}")
    for trigger_id in available:
        trigger = state["triggers"].get(trigger_id)
        if not trigger:
            print(f"[tick] trigger={trigger_id} -> skipped reason=no trigger found in state")
            continue

        suppression_key = trigger.get("suppression_key") or f"trigger:{trigger_id}"
        if suppression_key in state["sent_suppressions"]:
            print(f"[tick] trigger={trigger_id} kind={trigger.get('kind')} -> skipped reason=suppression_key already sent ({suppression_key})")
            continue

        merchant_id = trigger.get("merchant_id")
        merchant = state["merchants"].get(merchant_id)
        if merchant is None:
            print(f"[tick] trigger={trigger_id} kind={trigger.get('kind')} -> skipped reason=no merchant found for merchant_id={merchant_id}")
            continue

        category_slug = merchant.get("category_slug")
        category = state["categories"].get(category_slug)
        if category is None:
            print(f"[tick] trigger={trigger_id} kind={trigger.get('kind')} merchant={merchant_id} -> skipped reason=no category found for category_slug={category_slug}")
            continue

        customer_id = trigger.get("customer_id")
        customer = state["customers"].get(customer_id) if customer_id else None
        if customer_id and customer is None:
            print(f"[tick] trigger={trigger_id} kind={trigger.get('kind')} -> skipped reason=no customer found for customer_id={customer_id}")
            continue

        try:
            message = compose(category, merchant, trigger, customer)
        except Exception as exc:
            print(f"[tick] trigger={trigger_id} kind={trigger.get('kind')} -> skipped reason=compose() exception: {type(exc).__name__}: {exc}", file=sys.stderr)
            traceback.print_exc()
            continue

        body = message.get("body")
        if is_invalid_body(body):
            reason = "empty body" if not body else "body failed validity filter"
            print(f"[tick] trigger={trigger_id} kind={trigger.get('kind')} -> skipped reason={reason}; body={body[:140] if body else body!r}")
            continue

        conversation_id = f"conv_{uuid.uuid4().hex[:10]}"
        state["conversation_store"][conversation_id] = {
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "trigger_id": trigger_id,
            "trigger_kind": trigger.get("kind"),
            "trigger": trigger,
            "category": category,
            "merchant": merchant,
            "customer": customer,
            "auto_reply_count": 0,
            "bodies": [body],
        }

        state["sent_suppressions"].add(suppression_key)
        actions.append({
            "conversation_id": conversation_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": message["send_as"],
            "trigger_id": trigger_id,
            "template_name": "vera_contextual_v1",
            "template_params": [merchant.get("identity", {}).get("name", "Merchant"), body],
            "body": body,
            "cta": message["cta"],
            "suppression_key": message["suppression_key"],
            "rationale": message["rationale"],
        })
        print(f"[tick] trigger={trigger_id} kind={trigger.get('kind')} merchant={merchant_id} -> action accepted cta={message['cta']} suppression_key={message['suppression_key']}")

        if len(actions) >= MAX_ACTIONS_PER_TICK:
            print(f"[tick] {MAX_ACTIONS_PER_TICK}-action cap reached; remaining triggers stay queued for the next tick")
            break

    print(f"[tick] complete: {len(actions)} action(s) generated")
    return {"actions": actions}


def _draft_next_step(conv: Dict[str, Any]) -> str:
    merchant = conv.get("merchant") or {}
    trigger = conv.get("trigger") or {}
    trigger_kind = conv.get("trigger_kind") or trigger.get("kind") or ""
    if trigger_kind in CUSTOMER_TRIGGER_KINDS:
        customer = conv.get("customer") or {}
        customer_name = ((customer.get("identity") or {}).get("name")) or "the customer"
        return f"Request sent to {customer_name} — I'll update this thread the moment they confirm. Want the message copy here as well?"
    drafts = {
        "research_digest": "Draft ready — a short WhatsApp note for your team plus a GBP caption on this update. Want it here as a copy-paste block, or scheduled for tomorrow 10am?",
        "regulation_change": "Draft ready — the compliance note: what changes, the deadline, and a 2-line checklist for your team. Want it here now, or scheduled for tomorrow morning?",
        "cde_opportunity": "Draft ready — the registration note with date, credits, and fee details. Want it here as text, or shall I hold it for your Monday review?",
        "perf_dip": "Draft ready — the recovery offer and a fresh post, sized to your current numbers. Want it here now, or scheduled for tomorrow 10am?",
        "perf_spike": "Draft ready — the packaged offer and a follow-up post to ride the lift. Want it here now, or scheduled for tomorrow 10am?",
        "milestone_reached": "Draft ready — a short review-request note for this week's visitors. Want the copy-paste block here, or auto-scheduled after each visit?",
        "competitor_opened": "Draft ready — the value-bundle counter and a fresh post for your area. Want it here now, or scheduled for tomorrow 10am?",
        "renewal_due": "Renewal plan is ready to review — tell me a good time and I'll walk you through it before the deadline.",
        "festival_upcoming": "Draft ready — the festive offer and the GBP caption. Want the copy-paste block here, or scheduled for tomorrow morning?",
        "ipl_match_today": "Draft ready — the match-night pre-order message and the delivery push. Want it here now, or timed to go out this evening?",
        "category_seasonal": "Draft ready — the shelf and offer plan around the demand shift. Want it here now, or scheduled for tomorrow morning?",
        "gbp_unverified": "Draft ready — the verification steps and the 3-line checklist on what to keep ready. Want it here now?",
        "review_theme_emerged": "Draft ready — the reply template for the review theme plus the two ops changes. Want it here now, or scheduled as a reminder for tomorrow?",
        "supply_alert": "Draft ready — the patient alert and the batch note. This one is time-sensitive; want it here right now?",
        "winback_eligible": "Draft ready — the reactivation flow: target list and message copy. Want it here now, or scheduled for tomorrow morning?",
        "dormant_with_vera": "Draft ready — the winback offer and the fresh post. Want both here now, or scheduled for tomorrow 10am?",
        "curious_ask_due": "Noted — I'll build next week's offer around that. Want a first draft of the copy today?",
        "active_planning_intent": "Draft ready — the structure, the pricing copy, and the first post. Want it here now, or scheduled for tomorrow 10am?",
    }
    return drafts.get(trigger_kind, "Draft ready — want it here as a copy-paste block, or scheduled for tomorrow 10am?")


def _remember_sent(conv: Dict[str, Any], body: str) -> str:
    history = conv.setdefault("bodies", [])
    if body in history:
        body = f"Update: {body}"
    history.append(body)
    return body


@app.post("/v1/reply")
def handle_reply(payload: ReplyPayload):
    text = (payload.message or "").lower()
    conv = state["conversation_store"].get(payload.conversation_id)
    if conv is None:
        conv = {
            "merchant_id": payload.merchant_id,
            "customer_id": payload.customer_id,
            "merchant": state["merchants"].get(payload.merchant_id),
            "bodies": [],
            "auto_reply_count": 0,
        }
        state["conversation_store"][payload.conversation_id] = conv

    if matches_patterns(text, AUTO_REPLY_PATTERNS):
        conv["auto_reply_count"] = int(conv.get("auto_reply_count") or 0) + 1
        count = conv["auto_reply_count"]
        # Escalate by both the per-conversation count and the turn number, so the
        # send -> wait -> end ladder also holds when each canned reply arrives on
        # a fresh conversation id whose turn number keeps increasing.
        stage = max(count, min(max((payload.turn_number or 1) - 1, 0), 3))
        if stage <= 1:
            return {
                "action": "send",
                "body": "Looks like an auto-reply. When the owner sees this, just reply YES and I'll keep the draft ready to send.",
                "cta": "binary_yes_no",
                "rationale": "First canned reply — one calm nudge that flags the auto-reply and gives the owner a one-tap way back.",
            }
        if stage == 2:
            return {
                "action": "wait",
                "wait_seconds": 86400,
                "rationale": "Second canned reply — backing off for 24 hours instead of burning another message on an unmonitored inbox.",
            }
        return {
            "action": "end",
            "rationale": "Third canned reply — the inbox is clearly not monitored; ending the thread to protect the merchant's message limits.",
        }

    if matches_patterns(text, STOP_PATTERNS):
        return {"action": "end", "rationale": "Merchant asked to stop; ending the conversation respectfully."}

    merchant = conv.get("merchant") or state["merchants"].get(payload.merchant_id or "")
    name = ((merchant or {}).get("identity") or {}).get("name") or "there"

    if matches_patterns(text, YES_PATTERNS):
        body = _remember_sent(conv, _draft_next_step(conv))
        return {
            "action": "send",
            "body": body,
            "cta": "open_ended",
            "rationale": "Merchant accepted — delivering the drafted next step immediately instead of repeating the original ask.",
        }

    if matches_patterns(text, WAIT_PATTERNS):
        return {"action": "wait", "wait_seconds": 1800, "rationale": "Merchant asked for time; pause for half an hour and revisit later."}

    if payload.turn_number and payload.turn_number > 3:
        return {"action": "end", "rationale": "Conversation reached a reasonable stopping point after multiple low-value turns."}

    fallback = (
        f"Hi {name}, that one is outside what I can help with — but on your original thread: "
        f"want the short version or the full draft of what I sent?"
    )
    body = _remember_sent(conv, fallback)
    return {
        "action": "send",
        "body": body,
        "cta": "open_ended",
        "rationale": "Keeps the original thread alive with a distinct, low-friction question instead of repeating the same message.",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=False)
