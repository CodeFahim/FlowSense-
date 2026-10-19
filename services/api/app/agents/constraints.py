"""Hard constraints for assistant answers: the active policy plus limits the user stated in this chat.

The model is told these on every turn (in the business snapshot), and every live answer is checked by
server code afterwards: a line that recommends lowering the protected reserve, or borrowing when borrowing
is not allowed, is relabelled as outside the rules instead of being shown as a recovery option. Execution
is still guarded separately by the safety kernel; this module only keeps advice honest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.domain.position import active_policy
from app.money import fmt

# User statements that set a constraint for the rest of the conversation.
_KEEP_RESERVE = re.compile(
    r"\b(keep|protect|preserv\w*|leave|maintain)\w*\b[^.?!\n]{0,40}\breserve"
    r"|\breserve\b[^.?!\n]{0,40}\b(protected|untouched|intact|off[- ]limits|stays?|must stay|remain\w*)"
    r"|\b(don'?t|do not|never|without)\b[^.?!\n]{0,30}\b(touch\w*|lower\w*|reduc\w*|use|using|dip\w*|cut\w*)\b[^.?!\n]{0,30}\breserve",
    re.I,
)
_NO_BORROW = re.compile(
    r"\b(don'?t|do not|never|without|no)\b[^.?!\n]{0,20}\b(borrow\w*|loans?|financing|credit|debt)\b",
    re.I,
)
# Answer lines that recommend breaking a constraint. The reserve must be the object of the verb (at most three
# words between them): "lower the reserve", "use part of your reserve". A line that only mentions the reserve
# near another verb ("reduce what you pay now so your reserve stays intact") is not a recommendation to break it.
# Which options are allowed is decided by agents.options; this check is a backstop for the model's wording.
_LOWER_RESERVE = re.compile(
    r"\b(lower\w*|reduc\w*|cut\w*|decreas\w*|releas\w*|spend\w*|tap\w*|dip\w* into|draw\w* (?:on|from|down)|us(?:e|ing))\s+(?:\S+\s+){0,3}?(?:protected |safety |cash )?reserve\b"
    r"|\breserve\s+(?:could|can|should|might|may)\s+(?:be\s+)?(?:temporarily\s+)?(lowered|reduced|used|cut|released|spent)",
    re.I,
)
_KEPT = re.compile(r"\breserve\s+(?:\S+\s+)?(stays?|remains?|untouched|intact|protected)\b", re.I)
_BORROW = re.compile(r"\b(borrow\w*|take (out )?a loan|short[- ]term loan|overdraft|credit line|line of credit|cash advance|financing)\b", re.I)
# Mentions that rule borrowing out ("avoiding borrowing", "without a loan", "no financing needed") are removed
# before the borrowing check, so only a recommendation to borrow is relabelled.
_NEG_BORROW = re.compile(
    r"\b(no|not|without|avoid\w*|instead of|rather than|never|don'?t|do not|free of)\b\W+(?:\w+\W+){0,3}?"
    r"(borrow\w*|take (out )?a loan|loans?|short[- ]term loan|overdraft|credit line|line of credit|cash advance|financing|debt)\b",
    re.I,
)
# Words showing the line already treats it as outside the rules (a hypothetical policy change, not advice).
_ALREADY_FLAGGED = re.compile(
    r"\b(not (a |an )?(allowed|compliant|recovery|option|recommended|permitted)|isn'?t (allowed|an option|permitted)|outside (your|the) (rules|constraints|policy)|"
    r"would (break|breach|violate)|breach\w*|violat\w*|can'?t|cannot|not possible|policy change|only the owner|exceeds?|above your|over your|"
    r"prohibit\w*|forbid\w*|unavailable|not available|rejected|ruled out|off[- ]limits|not an? (practical |safe )?option)\b",
    re.I,
)
FLAG = "Prohibited by your rules"  # the same words as the "prohibited" class in agents.options
FLAG_REASONS = {"reserve": "protected reserve", "borrowing": "no borrowing"}


def flag_text(hit: str) -> str:
    return f"{FLAG} ({FLAG_REASONS[hit]}):"


@dataclass
class Constraints:
    reserve_minor: int | None
    max_financing_bp: int | None
    per_payment_limit_minor: int | None
    policy_version: int | None
    user_keep_reserve: bool
    user_no_borrowing: bool

    @property
    def reserve_protected(self) -> bool:
        return self.user_keep_reserve or bool(self.reserve_minor)

    @property
    def borrowing_blocked(self) -> bool:
        return self.user_no_borrowing or self.max_financing_bp == 0


def from_conversation(texts: list[str]) -> tuple[bool, bool]:
    """Constraints the user stated in earlier or current messages of this chat (deterministic)."""
    keep = any(_KEEP_RESERVE.search(t) for t in texts)
    no_borrow = any(_NO_BORROW.search(t) for t in texts)
    return keep, no_borrow


def current(db: Any, biz: Any, user_texts: list[str]) -> Constraints:
    pol = active_policy(db, biz.id)
    spec = pol.spec if pol else {}
    keep, no_borrow = from_conversation(user_texts)
    limit = spec.get("per_transaction_limit_minor")
    return Constraints(
        reserve_minor=spec.get("reserve_minor") if pol else None,
        max_financing_bp=spec.get("max_financing_annualized_bp") if pol else None,
        per_payment_limit_minor=limit if isinstance(limit, int) else None,
        policy_version=pol.version if pol else None,
        user_keep_reserve=keep,
        user_no_borrowing=no_borrow,
    )


def describe(c: Constraints, ccy: str | None) -> dict[str, Any]:
    """What the model must respect on this turn (added to the business snapshot)."""
    rules: list[str] = []
    if c.reserve_minor:
        rules.append(f"Protected reserve {fmt(c.reserve_minor, ccy or '')} (policy v{c.policy_version}): it is not spendable. Lowering it is never a recovery option.")
    if c.user_keep_reserve:
        rules.append("The user said in this chat that the reserve must stay protected. Keep this for every later answer, including hypotheticals.")
    if c.max_financing_bp == 0:
        rules.append("Borrowing is not allowed by the policy (financing cost cap 0%).")
    elif c.max_financing_bp:
        rules.append(f"Financing is acceptable only at or below {c.max_financing_bp / 100:.2f}% annualized all-in cost, and only when a real quote exists in the rescue plan.")
    if c.user_no_borrowing:
        rules.append("The user said in this chat not to borrow. Do not offer loans or credit.")
    if c.per_payment_limit_minor:
        rules.append(f"Per-payment limit {fmt(c.per_payment_limit_minor, ccy or '')}.")
    rules.append("Every payment needs the owner's approval and a fresh safety check; nothing bypasses it.")
    return {
        "rules": rules,
        "how_to_use": (
            "These are hard constraints. Recommend only actions that satisfy all of them. Give fewer options (or none) rather than "
            "an option that breaks one. If the user asks about changing a rule, discuss it separately, labelled as a policy change "
            "outside the current rules that only the owner can make in Settings, never as a recovery option."
        ),
    }


def guard(text: str, c: Constraints, ccy: str | None) -> tuple[str, list[str]]:
    """Relabel answer lines that recommend breaking a hard constraint. Returns the text and what was flagged."""
    flagged: list[str] = []
    out: list[str] = []
    ruled_out = False  # inside a section such as "Why other options are unavailable"
    for line in text.split("\n"):
        hit = None
        if re.match(r"^\s*(#+\s|\*\*)", line):
            ruled_out = bool(_ALREADY_FLAGGED.search(line) or re.search(r"\b(why not|not recommended|avoid)\b", line, re.I))
        if ruled_out:
            pass
        elif c.reserve_protected and _LOWER_RESERVE.search(line) and not _ALREADY_FLAGGED.search(line) and not _KEPT.search(line):
            hit = "reserve"
        elif c.borrowing_blocked and _BORROW.search(_NEG_BORROW.sub(" ", line)) and not _ALREADY_FLAGGED.search(line):
            hit = "borrowing"
        if hit:
            flagged.append(hit)
            m = re.match(r"^(\s*(?:[-*•]|\d+[.)])\s+)?(.*)$", line)
            assert m is not None
            line = f"{m.group(1) or ''}*{flag_text(hit)}* {m.group(2)}"
        out.append(line)
    if not flagged:
        return text, []
    notes = []
    if "reserve" in flagged:
        amount = f" of {fmt(c.reserve_minor, ccy or '')}" if c.reserve_minor else ""
        notes.append(f"the protected reserve{amount} stays untouched")
    if "borrowing" in flagged:
        notes.append("borrowing is not allowed" if c.max_financing_bp == 0 or c.max_financing_bp is None else "no borrowing, as you asked")
    out.append("")
    out.append(f"**Rule check (FlowSense):** under your current rules {' and '.join(notes)}. Changing a rule is a policy decision only the owner can make in Settings; it is not part of this plan.")
    return "\n".join(out), sorted(set(flagged))
