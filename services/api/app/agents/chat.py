"""Assistant conversations: mode selection, history, limits and the response shape.

- Mode: live (Anthropic or Gemini, tool-grounded) when configured; the offline demo assistant when the
  server is in demo mode or when the user explicitly asks for it (always labelled).
- When the live AI is out of quota, overloaded, slow or unreachable, or the user's daily AI limit is
  reached, the answer comes from FlowSense's own calculations instead (ai_auto_fallback). It is labelled
  "Quick answer" with the reason, never presented as AI. Configuration problems (key, model, permission)
  are still reported as "AI unavailable" so the operator sees them.
- The same question on unchanged records (same snapshot, same recent turns) within a few minutes reuses
  the earlier AI answer: faster, and it saves the provider's quota.
- History is stored server-side as separate chats per (user, business): never in browser storage, never
  shared with another user or workspace. A user can start, open, rename and delete chats; another user's
  chat id looks exactly like a missing one (404). Messages older than RETENTION_DAYS are deleted.
- Limits: per-user daily cap on AI answers (quick answers and cached answers don't count); bounded tool
  rounds, time and tokens inside the live loop.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.agents import assistant as demo
from app.agents import constraints, live, options, proposals
from app.agents import tools as T
from app.audit import chain as audit
from app.auth import ratelimit
from app.clock import real_now
from app.config import get_settings
from app.domain.actions import Actor
from app.domain.workspace import is_configured
from app.errors import ApiError, not_found
from app.models import AssistantConversation, AssistantMessage, Business, User

RETENTION_DAYS = 30
HISTORY_TURNS = 8

# Transient AI problems that a quick answer from FlowSense's own calculations can cover.
_TRANSIENT = {"rate_limited", "provider_down", "timeout", "network"}
QUICK_LABEL = "Quick answer (from your records)"
_NOTICES = {
    "rate_limited": "The AI's free usage limit is reached for now, so FlowSense answered from its own calculations. Full AI answers come back automatically.",
    "provider_down": "The AI service is overloaded right now, so FlowSense answered from its own calculations. Full AI answers come back automatically.",
    "timeout": "The AI took too long, so FlowSense answered from its own calculations. Ask again for a full AI answer.",
    "network": "The AI service couldn't be reached, so FlowSense answered from its own calculations. Ask again for a full AI answer.",
    "user_limit": "You've used today's AI answers, so FlowSense answered from its own calculations. Full AI answers are available again within 24 hours.",
}
# (business, user, question, recent turns, records) -> (expires at, reply). One API process: memory is enough.
_answer_cache: dict[str, tuple[float, dict[str, Any]]] = {}


MAX_CHATS = 50
NEW_TITLE = "New chat"


def _owned(db: Session, user: User, biz: Business, conv_id: str | None) -> AssistantConversation | None:
    """A chat of this user in this workspace, or None. Never another user's or workspace's chat."""
    if not conv_id:
        return None
    try:
        cid = uuid.UUID(str(conv_id))
    except ValueError:
        raise not_found("Chat") from None
    conv = db.get(AssistantConversation, cid)
    if conv is None or conv.user_id != user.id or conv.business_id != biz.id:
        raise not_found("Chat")
    return conv


def _latest(db: Session, user: User, biz: Business) -> AssistantConversation | None:
    return db.execute(
        select(AssistantConversation)
        .where(AssistantConversation.user_id == user.id, AssistantConversation.business_id == biz.id)
        .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _new(db: Session, user: User, biz: Business) -> AssistantConversation:
    n = db.execute(select(func.count(AssistantConversation.id)).where(AssistantConversation.user_id == user.id, AssistantConversation.business_id == biz.id)).scalar_one()
    if n >= MAX_CHATS:
        raise ApiError(409, "TOO_MANY_CHATS", f"You have {MAX_CHATS} chats. Delete some old ones to start a new chat.")
    conv = AssistantConversation(user_id=user.id, business_id=biz.id, title=NEW_TITLE)
    db.add(conv)
    db.flush()
    return conv


def _conversation(db: Session, user: User, biz: Business, create: bool = True, conv_id: str | None = None) -> AssistantConversation | None:
    conv = _owned(db, user, biz, conv_id) or _latest(db, user, biz)
    if conv is None and create:
        conv = _new(db, user, biz)
    return conv


def _prune(db: Session, conv: AssistantConversation) -> None:
    db.execute(delete(AssistantMessage).where(AssistantMessage.conversation_id == conv.id, AssistantMessage.created_at < real_now() - timedelta(days=RETENTION_DAYS)))


def _chat_view(db: Session, c: AssistantConversation) -> dict[str, Any]:
    n = db.execute(select(func.count(AssistantMessage.id)).where(AssistantMessage.conversation_id == c.id)).scalar_one()
    return {"id": str(c.id), "title": c.title or NEW_TITLE, "updated_at": (c.updated_at or c.created_at).isoformat(), "created_at": c.created_at.isoformat(), "messages": int(n)}


def list_chats(db: Session, user: User, biz: Business) -> list[dict[str, Any]]:
    rows = db.execute(
        select(AssistantConversation)
        .where(AssistantConversation.user_id == user.id, AssistantConversation.business_id == biz.id)
        .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.created_at.desc())
    ).scalars().all()
    return [_chat_view(db, c) for c in rows]


def new_chat(db: Session, user: User, biz: Business) -> dict[str, Any]:
    # Reuse an empty chat instead of piling up blank ones.
    latest = _latest(db, user, biz)
    if latest is not None and _chat_view(db, latest)["messages"] == 0:
        return _chat_view(db, latest)
    conv = _new(db, user, biz)
    db.commit()
    return _chat_view(db, conv)


def _clean_title(title: str) -> str:
    t = " ".join("".join(ch if ch.isprintable() else (" " if ch.isspace() else "") for ch in title).split())[:80]
    if not t:
        raise ApiError(422, "TITLE_REQUIRED", "Give the chat a name.")
    return t


def rename_chat(db: Session, user: User, biz: Business, conv_id: str, title: str) -> dict[str, Any]:
    conv = _owned(db, user, biz, conv_id)
    assert conv is not None
    conv.title = _clean_title(title)
    db.commit()
    return _chat_view(db, conv)


def delete_chat(db: Session, user: User, biz: Business, conv_id: str) -> dict[str, Any]:
    conv = _owned(db, user, biz, conv_id)
    assert conv is not None
    n = db.execute(select(func.count(AssistantMessage.id)).where(AssistantMessage.conversation_id == conv.id)).scalar_one()
    db.execute(delete(AssistantMessage).where(AssistantMessage.conversation_id == conv.id))
    db.delete(conv)
    audit.append(db, biz.id, event_type="assistant.chat_deleted", actor_type="user", actor_id=user.id, payload={"messages": int(n)})
    db.commit()
    return {"deleted": True, "messages": int(n)}


def history(db: Session, user: User, biz: Business, limit: int = 50, conv_id: str | None = None) -> list[dict[str, Any]]:
    conv = _conversation(db, user, biz, create=False, conv_id=conv_id)
    if conv is None:
        return []
    rows = db.execute(
        select(AssistantMessage).where(AssistantMessage.conversation_id == conv.id).order_by(AssistantMessage.id.desc()).limit(limit)
    ).scalars().all()
    return [{"id": r.id, "role": r.role, "created_at": r.created_at.isoformat(), **r.content} for r in reversed(rows)]


def conversation(db: Session, user: User, biz: Business, conv_id: str | None) -> dict[str, Any]:
    conv = _conversation(db, user, biz, create=False, conv_id=conv_id)
    return {
        "chat": _chat_view(db, conv) if conv else None,
        "messages": history(db, user, biz, conv_id=str(conv.id)) if conv else [],
        "retention_days": RETENTION_DAYS,
    }


def clear(db: Session, user: User, biz: Business) -> int:
    """Delete every chat of this user in this workspace."""
    convs = db.execute(select(AssistantConversation).where(AssistantConversation.user_id == user.id, AssistantConversation.business_id == biz.id)).scalars().all()
    n = 0
    for conv in convs:
        n += len(db.execute(select(AssistantMessage.id).where(AssistantMessage.conversation_id == conv.id)).all())
        db.execute(delete(AssistantMessage).where(AssistantMessage.conversation_id == conv.id))
        db.delete(conv)
    audit.append(db, biz.id, event_type="assistant.history_cleared", actor_type="user", actor_id=user.id, payload={"messages": int(n)})
    db.commit()
    return int(n)


def status(biz: Business) -> dict[str, Any]:
    s = get_settings()
    configured = s.ai_mode == "live"
    return {
        "mode": s.ai_mode,
        "label": demo.mode_label(),
        "live_configured": configured,
        "provider": s.ai_provider if configured else None,
        "model": s.ai_model if configured else None,
        "demo_available": True,
        "suggestions": demo.suggestions(biz.currency or "", fresh=not is_configured(biz) or biz.cash_declared_at is None),
        "quick_actions": demo.QUICK_ACTIONS,
        "retention_days": RETENTION_DAYS,
        "limits": {"messages_per_day": s.ai_messages_per_user_per_day, "max_tool_rounds": s.ai_max_tool_rounds, "timeout_seconds": s.ai_timeout_seconds},
    }


def _summary(calls: list[str]) -> str | None:
    if not calls:
        return None
    seen: list[str] = []
    for c in calls:
        if c not in seen:
            seen.append(c)
    text = ", ".join(seen)
    return text[:1].upper() + text[1:] + "."


def _followups(biz: Business, message: str) -> list[str]:
    """Three natural next questions (never the one just asked), for the buttons under every answer."""
    fresh = not is_configured(biz) or biz.cash_declared_at is None
    pool = demo.suggestions(biz.currency or "", fresh=fresh)["primary"]
    if not fresh:
        pool = [*demo.QUICK_ACTIONS, *pool]
    asked = re.sub(r"[^a-z0-9 ]", "", message.lower()).strip()
    out: list[str] = []
    for q in pool:
        if re.sub(r"[^a-z0-9 ]", "", q.lower()).strip() != asked and q not in out:
            out.append(q)
    return out[:3]


def _quick_answer(db: Session, user: User, biz: Business, role: str, message: str, reason: str) -> dict[str, Any]:
    """FlowSense's own deterministic answer from the user's records, labelled with why the AI didn't answer."""
    out = demo.chat(db, biz, Actor(user.id, role, "assistant"), message)
    text = out["text"]
    if out.get("intent") == "unsupported":
        text = "I can only give quick answers from your records right now, and I can't answer that one in this mode. Ask again in a few minutes for a full AI answer, or pick one of these:"
    return {
        "mode": "quick",
        "label": QUICK_LABEL,
        "notice": _NOTICES[reason],
        "text": text,
        "links": out.get("links", []),
        "citations": [{"kind": "ref", "ref": c, "label": c, "href": ""} for c in out.get("citations", [])],
        "drafts": [],
        "proposals": out.get("proposals") or [],
        "activity": None,
        "suggestions": (out.get("suggestions") or _followups(biz, message))[:5],
        "intent": out.get("intent"),
        "decision": out.get("decision"),
        "proposed_policy": out.get("proposed_policy"),
        "fallback_reason": reason,
        "options": _deterministic_options(db, biz, out.get("intent"), [message]),
    }


_ASKS_FOR_OPTIONS = re.compile(
    r"\b(options?|alternatives?|choices?|ways? (to|out|of)|what (can|should|could) i do|how (can|do|could) i (cover|pay|close|fix|fill)|rescue plan|plan to cover)\b",
    re.I,
)


# Answers about options or the shortfall carry the same deterministic option cards in every mode, so they stay
# available when the live AI is not (quick answers) and in the offline demo assistant.
_OPTION_INTENTS = {"compare_options", "explain_gap", "plan_payments", "payroll_safety", "compare_financing"}


def _deterministic_options(db: Session, biz: Business, intent: Any, user_texts: list[str]) -> dict[str, Any] | None:
    if intent not in _OPTION_INTENTS or not is_configured(biz):
        return None
    return options.card_view(options.assess(db, biz, user_texts))


def _cache_key(ctx: T.ToolContext, message: str, page: str | None, prior: list[dict[str, Any]]) -> str:
    snapshot, _ = T.run_tool(ctx, "get_business_snapshot", {})
    recent = [(m["role"], m.get("text", "")) for m in prior[-2:]]
    raw = json.dumps([str(ctx.biz.id), str(ctx.user.id), ctx.role, " ".join(message.lower().split()), page, recent, snapshot], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def send(db: Session, user: User, biz: Business, role: str, message: str, page: str | None, use_demo: bool, conv_id: str | None = None) -> dict[str, Any]:
    s = get_settings()
    # Spam guard only (generous); the AI-answer limit below counts real AI answers.
    ratelimit.check(db, ratelimit.bucket("assistant-burst", str(user.id)), 30, timedelta(minutes=1), "That's a lot of messages in a minute. Wait a moment and try again.")
    ai_day = ratelimit.bucket("assistant-day", str(user.id))
    message = message.strip()[:2000]
    if not message:
        raise ApiError(422, "MESSAGE_EMPTY", "Type a question first.")
    conv = _conversation(db, user, biz, conv_id=conv_id)
    assert conv is not None
    _prune(db, conv)
    prior = history(db, user, biz, limit=HISTORY_TURNS * 2, conv_id=str(conv.id))
    if not conv.title or conv.title == NEW_TITLE:
        conv.title = _clean_title(message[:60] + ("…" if len(message) > 60 else ""))
    mode = "demo" if (use_demo or s.ai_mode == "demo") else s.ai_mode
    if mode == "off":
        raise live.AIUnavailable("off", "The AI assistant is turned off on this server. You can use the labelled demo assistant instead.")
    db.add(AssistantMessage(conversation_id=conv.id, business_id=biz.id, user_id=user.id, role="user", content={"text": message, "page": page}))
    db.commit()
    reserved: ratelimit.RateLimitHit | None = None
    over_limit = False
    if mode == "live":
        # Count and reserve under a lock on the user, so simultaneous requests can't all pass the same count.
        db.execute(select(User.id).where(User.id == user.id).with_for_update())
        over_limit = ratelimit.hits(db, ai_day, timedelta(days=1)) >= s.ai_messages_per_user_per_day
        if not over_limit:
            reserved = ratelimit.RateLimitHit(bucket=ai_day)
            db.add(reserved)
        db.commit()
    if over_limit and not s.ai_auto_fallback:
        raise ApiError(429, "RATE_LIMITED", "You reached today's assistant message limit. It resets within 24 hours.", extra={"retry_after_minutes": 1440})
    if mode == "live" and over_limit:
        reply = _quick_answer(db, user, biz, role, message, "user_limit")
    elif mode == "demo":
        out = demo.chat(db, biz, Actor(user.id, role, "assistant"), message)
        reply = {
            "mode": "demo",
            "label": demo.LABEL,
            "text": out["text"],
            "links": out.get("links", []),
            "citations": [{"kind": "ref", "ref": c, "label": c, "href": ""} for c in out.get("citations", [])],
            "drafts": [],
            "proposals": out.get("proposals") or [],
            "activity": None,
            "suggestions": out.get("suggestions") or [],
            "intent": out.get("intent"),
            "decision": out.get("decision"),
            "proposed_policy": out.get("proposed_policy"),
            "explicit_demo": bool(use_demo and s.ai_mode != "demo"),
            "options": _deterministic_options(db, biz, out.get("intent"), _user_texts(db, conv.id, message)),
        }
    else:
        user_texts = _user_texts(db, conv.id, message)
        ctx = T.ToolContext(db=db, biz=biz, user=user, role=role, user_texts=user_texts)
        snapshot_at = real_now()
        key = _cache_key(ctx, message, page, prior) if s.ai_answer_cache_minutes > 0 else ""
        cached = _answer_cache.get(key) if key else None
        if cached and cached[0] > time.monotonic():
            reply = {**cached[1], "cached": True}
        else:
            ctx = T.ToolContext(db=db, biz=biz, user=user, role=role, user_texts=user_texts)
            try:
                reply = _live_reply(db, user, biz, role, ctx, message, page, prior, snapshot_at)
            except live.AIUnavailable:
                _release(db, reserved)  # configuration problems: nothing was answered
                raise
            # Answers that prepared something for the user to confirm are never reused.
            if reply.get("mode") == "live" and key and not reply["proposals"] and not reply["drafts"]:
                _answer_cache[key] = (time.monotonic() + s.ai_answer_cache_minutes * 60, dict(reply))
                if len(_answer_cache) > 500:
                    now = time.monotonic()
                    for k in [k for k, (exp, _) in _answer_cache.items() if exp <= now][:250]:
                        _answer_cache.pop(k, None)
    # The reserved AI answer is given back when the request cost nothing: a reused answer, or a refusal for
    # quota, overload or network before any answer. A timeout may have used tokens, so it still counts.
    if reserved is not None and (reply.get("cached") or reply.get("fallback_reason") in ("rate_limited", "provider_down", "network")):
        _release(db, reserved)
    stored = {k: v for k, v in reply.items() if k in ("mode", "label", "text", "links", "citations", "drafts", "proposals", "activity", "suggestions", "based_on_records_at", "explicit_demo", "notice", "options")}
    db.add(AssistantMessage(conversation_id=conv.id, business_id=biz.id, user_id=user.id, role="assistant", content=stored))
    conv.updated_at = real_now()
    db.commit()
    reply["chat"] = _chat_view(db, conv)
    return reply


def _user_texts(db: Session, conv_id: Any, message: str) -> list[str]:
    """Every user message in this chat (newest 50) plus the current one: constraints stated earlier still apply."""
    rows = db.execute(
        select(AssistantMessage.content)
        .where(AssistantMessage.conversation_id == conv_id, AssistantMessage.role == "user")
        .order_by(AssistantMessage.created_at.desc())
        .limit(50)
    ).scalars().all()
    texts = [str((c or {}).get("text", "")) for c in rows]
    return [*texts, message]


def _release(db: Session, hit: ratelimit.RateLimitHit | None) -> None:
    if hit is not None and hit.id is not None:
        db.execute(delete(ratelimit.RateLimitHit).where(ratelimit.RateLimitHit.id == hit.id))
        db.commit()


def _live_reply(db: Session, user: User, biz: Business, role: str, ctx: T.ToolContext, message: str, page: str | None, prior: list[dict[str, Any]], snapshot_at: Any) -> dict[str, Any]:
    s = get_settings()
    try:
        turn = live.run(ctx, message, [{"role": m["role"], "text": m.get("text", "")} for m in prior], page)
    except live.AIUnavailable as err:
        category = str(err.extra.get("category"))
        audit.append(db, biz.id, event_type="assistant.turn", actor_type="assistant", actor_id=user.id, outcome="unavailable", payload={"mode": "live", "category": category, "fallback": s.ai_auto_fallback and category in _TRANSIENT})
        db.commit()
        if s.ai_auto_fallback and category in _TRANSIENT:
            return _quick_answer(db, user, biz, role, message, category)
        raise
    # Server-side check: a line recommending a break of a hard constraint is relabelled, never shown as an option.
    turn_text = turn.text
    if turn_text == live.EMPTY_ANSWER and ctx.links:
        # Live check, 19 Oct: "Where do I add a new bill?" ended with the right button but no text. Point to it.
        turn_text = f"Use the **{ctx.links[-1]['label']}** button below."
    rules = constraints.current(db, biz, ctx.user_texts)
    text, flagged = constraints.guard(turn_text, rules, biz.currency)
    # Option cards come from FlowSense's own classification: shown when the model assessed options, or when the
    # guard had to relabel a line (so the user sees the deterministic classes next to the corrected text).
    assessed = ctx.assessment
    if assessed is None and (flagged or _ASKS_FOR_OPTIONS.search(message)):
        # Questions about options or ways out get FlowSense's own classification whether or not the model called
        # assess_options (live check, 19 Oct: "What are my options to cover payroll?" had no cards).
        candidate = options.assess(db, biz, ctx.user_texts)
        if flagged or candidate.get("status") != "no_shortfall":
            assessed = candidate
    reply = {
        "mode": "live",
        "label": "Live AI",
        "text": text,
        "links": ctx.links[:6],
        "citations": ctx.citations[:12],
        "drafts": ctx.drafts[:3],
        "proposals": ctx.proposals[:3],
        "activity": _summary(ctx.activity),
        "tool_calls": turn.tool_calls,
        "suggestions": _followups(biz, message),
        "provider": s.ai_provider,
        "model": turn.model,
        "stop": turn.stop,
        "based_on_records_at": snapshot_at.isoformat(),
        "options": options.card_view(assessed) if assessed else None,
    }
    audit.append(
        db,
        biz.id,
        event_type="assistant.turn",
        actor_type="assistant",
        actor_id=user.id,
        outcome="answered",
        payload={"mode": "live", "provider": s.ai_provider, "tools": [c["tool"] for c in turn.tool_calls], "stop": turn.stop, "input_tokens": turn.usage["input_tokens"], "output_tokens": turn.usage["output_tokens"], "constraint_flags": flagged},
    )
    db.commit()
    return reply


def _note(db: Session, user: User, biz: Business, text: str, proposal: dict[str, Any]) -> None:
    """Record the verified outcome in the conversation, so the user and the next answer both see it."""
    conv = _conversation(db, user, biz)
    assert conv is not None
    db.add(AssistantMessage(conversation_id=conv.id, business_id=biz.id, user_id=user.id, role="assistant", content={"mode": "system", "label": "FlowSense", "text": text, "proposals": [proposal]}))
    db.commit()


def confirm_proposal(db: Session, user: User, biz: Business, role: str, proposal_id: str) -> dict[str, Any]:
    ratelimit.check(db, ratelimit.bucket("proposal-confirm", str(user.id)), 60, timedelta(hours=1), "Too many confirmations. Try again later.")
    db.commit()
    try:
        view = proposals.confirm(db, biz.id, user, role, proposal_id)
    except ApiError as exc:
        if exc.code not in ("NOT_FOUND", "PROPOSAL_DONE"):
            _note(db, user, biz, f"Not done: {exc.message}", {"id": proposal_id, "status": "failed"})
        raise
    res = view.get("result") or {}
    pos = (res.get("position") or {}).get("text")
    text = ("Done and checked: " if res.get("verified") else "Done, but FlowSense could not confirm the result: ") + str(res.get("message", ""))
    _note(db, user, biz, text + (f" {pos}" if pos else ""), view)
    return view
