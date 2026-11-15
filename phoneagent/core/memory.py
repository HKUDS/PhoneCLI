"""What the agent remembers between runs.

Deliberately small, but built on the four choices that make a memory store
usable rather than decorative -- all four taken from Mika's design:

1. **Fixed prefix over retrieval.** If everything fits in the budget it is all
   injected, in a stable order, so the prompt prefix stays identical between
   runs and the provider's context cache can hit. Retrieval is the fallback for
   when the store outgrows the budget, not the default.
2. **A sensitive filter with two sides.** Passwords and one-time codes are
   never stored; phone numbers, addresses and birthdays are, because those are
   what a phone assistant actually needs.
3. **A procedure is not a fact.** A reusable "how the user likes this done" is
   only injected after the user approved it; until then it is ``pending``.
4. **Every item keeps the sentence it came from.** When a memory is wrong, the
   quote is how you find out why.

Nothing here talks to a model: extraction lives in :func:`extract`, and the
store is plain data so it can be tested and inspected.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

# -- budgets ----------------------------------------------------------------

MAX_TOKENS = 800        # retrieval mode
FULL_MAX_TOKENS = 4_000  # everything
MAX_PINNED = 5
MAX_RELEVANT = 8
MAX_PROCEDURES = 3
MIN_SCORE = 0.3

FACTS_HEADER = (
    "Memories about the user (things the user told you; may be out of date; "
    "when they conflict with what the user says now, follow what they say now):"
)
PROCEDURES_HEADER = (
    "Ways of doing things the user approved (when they conflict with what the "
    "user says now, follow what they say now):"
)


# -- sensitive filtering ----------------------------------------------------

#: Anything that would be a credential if it leaked.
_SECRET_WORDS = (
    "支付密码", "密码", "口令", "验证码", "校验码", "动态码", "安全码", "安全问题",
    "password", "passcode", "passwd", "cvv", "cvc", "verification code",
    "security code", "security question", "one-time code",
)
#: Short words that are only secrets when a number follows them ("pin 1234"),
#: because "pinned" and "hotpot" are ordinary English.
_DIGIT_WORDS = ("pin", "otp")

#: Explicitly allowed: these are facts a phone assistant needs, not credentials.
_ALLOWED_FACTS = ("手机号", "电话", "地址", "生日", "家人", "称呼", "收货")


def is_sensitive(text: str) -> bool:
    """Would storing this be a credential leak?"""
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    for word in _SECRET_WORDS:
        if word in lowered:
            return True
    for word in _DIGIT_WORDS:
        if re.search(rf"(?<![a-z]){word}(?![a-z])", lowered) and re.search(
            rf"(?<![a-z]){word}(?![a-z])\s*[:：=是为]?\s*\d", lowered
        ):
            return True
    return False


# -- the item ---------------------------------------------------------------


@dataclass
class MemoryItem:
    id: str
    kind: str           # fact | preference | procedure
    content: str
    keywords: list[str] = field(default_factory=list)
    status: str = "active"   # active | pending (procedures only)
    pinned: bool = False
    source_type: str = "user_said"
    quote: str = ""          # the sentence this came from
    created_at: float = 0.0
    updated_at: float = 0.0
    last_used_at: float = 0.0
    use_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "MemoryItem":
        known = {k: raw.get(k) for k in cls.__dataclass_fields__ if k in raw}
        known.setdefault("id", "")
        known.setdefault("kind", "fact")
        known.setdefault("content", "")
        return cls(**known)


def _item_id(kind: str, content: str) -> str:
    return hashlib.sha1(f"{kind}:{content}".encode("utf-8")).hexdigest()[:12]


def _keywords(text: str) -> list[str]:
    """ASCII words plus CJK bigrams.

    Chinese has no spaces, so treating a run of characters as one token makes
    every overlap zero -- "打开蓝牙" and an item tagged 蓝牙 would never match.
    Sliding two-character windows give overlap something to count.
    """
    words = [word.lower() for word in re.findall(r"[A-Za-z]{2,}", text or "")]
    bigrams: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", text or ""):
        bigrams.extend(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return list(dict.fromkeys(words + bigrams))[:24]


# -- token budget -----------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Rough, and good enough: ASCII words one each, CJK one per character."""
    words = len(re.findall(r"[A-Za-z]+|\d+", text or ""))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    return words + cjk


# -- the store --------------------------------------------------------------


class MemoryStore:
    """Reads and writes one JSON file; no model, no lock beyond the caller."""

    def __init__(self, path: str = "", items: Optional[list[MemoryItem]] = None) -> None:
        self.path = path
        self.items: list[MemoryItem] = list(items or [])
        if path and not items:
            self.load()

    # -- persistence ---------------------------------------------------

    def load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self.items = [MemoryItem.from_dict(entry) for entry in raw.get("items", [])]
        except (OSError, ValueError, TypeError):
            self.items = []

    def save(self) -> None:
        if not self.path:
            return
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        from ..atomic import write_json

        write_json(self.path,
                   {"items": [item.to_dict() for item in self.items]},
                   indent=1)

    # -- writing -------------------------------------------------------

    def add(
        self,
        kind: str,
        content: str,
        quote: str = "",
        source_type: str = "user_said",
    ) -> Optional[MemoryItem]:
        """Store one memory; None means it was refused."""
        content = (content or "").strip()
        if not content:
            return None
        if is_sensitive(content) or is_sensitive(quote):
            return None
        existing = next(
            (item for item in self.items if item.kind == kind and item.content == content),
            None,
        )
        if existing is not None:
            return existing
        now = time.time()
        item = MemoryItem(
            id=_item_id(kind, content),
            kind=kind,
            content=content,
            keywords=_keywords(content),
            # a procedure changes how future runs behave, so the user approves it
            status="pending" if kind == "procedure" else "active",
            quote=quote,
            source_type=source_type,
            created_at=now,
            updated_at=now,
        )
        self.items.append(item)
        self.save()
        return item

    def approve(self, item_id: str) -> bool:
        for item in self.items:
            if item.id == item_id and item.status == "pending":
                item.status = "active"
                item.updated_at = time.time()
                self.save()
                return True
        return False

    def forget(self, item_id: str) -> bool:
        before = len(self.items)
        self.items = [item for item in self.items if item.id != item_id]
        if len(self.items) != before:
            self.save()
            return True
        return False

    def mark_used(self, ids: Iterable[str]) -> None:
        wanted = set(ids)
        if not wanted:
            return
        now = time.time()
        changed = False
        for item in self.items:
            if item.id in wanted:
                item.use_count += 1
                item.last_used_at = now
                changed = True
        if changed:
            self.save()

    # -- reading -------------------------------------------------------

    def active(self) -> list[MemoryItem]:
        return [item for item in self.items if item.status == "active"]

    def pending(self) -> list[MemoryItem]:
        return [item for item in self.items if item.status == "pending"]


# -- injection --------------------------------------------------------------


@dataclass
class MemoryBlock:
    message: str = ""
    used_ids: list[str] = field(default_factory=list)


def _score(item: MemoryItem, task_tokens: set[str]) -> float:
    if not task_tokens:
        return 0.0
    haystack = set(item.keywords) | set(_keywords(item.content))
    if not haystack:
        return 0.0
    # coverage of the *task*, not of the item: a short memory that covers a
    # third of what the task is about is relevant; a long one that happens to
    # contain a common word is not.
    return len(task_tokens & haystack) / max(1, len(task_tokens))


def build(
    items: list[MemoryItem],
    task: str,
    mode: str = "auto",
    max_tokens: int = MAX_TOKENS,
    full_max_tokens: int = FULL_MAX_TOKENS,
) -> MemoryBlock:
    """Render the memory block for one task.

    ``auto`` injects everything when it fits -- the fixed order keeps the prompt
    prefix stable, which is worth more than a smarter selection. Retrieval only
    takes over once the store outgrows the budget.
    """
    active = [item for item in items if item.status == "active"]
    if not active:
        return MemoryBlock()

    facts = [item for item in active if item.kind != "procedure"]
    procedures = [item for item in active if item.kind == "procedure"]

    def render(fact_items, proc_items) -> MemoryBlock:
        lines: list[str] = []
        used: list[str] = []
        if fact_items:
            lines.append(FACTS_HEADER)
            for item in fact_items:
                lines.append(f"- {item.content}")
                used.append(item.id)
        if proc_items:
            if lines:
                lines.append("")
            lines.append(PROCEDURES_HEADER)
            for item in proc_items:
                lines.append(f"- {item.content}")
                used.append(item.id)
        return MemoryBlock("\n".join(lines), used)

    full = render(facts, procedures[:MAX_PROCEDURES])
    if mode == "full":
        return full
    if mode == "auto" and estimate_tokens(full.message) <= full_max_tokens:
        return full

    # retrieval: pinned first, then whatever actually overlaps the task
    tokens = set(_keywords(task))
    pinned = [item for item in facts if item.pinned][:MAX_PINNED]
    pinned_ids = {item.id for item in pinned}
    scored = sorted(
        (item for item in facts if item.id not in pinned_ids),
        key=lambda item: _score(item, tokens),
        reverse=True,
    )
    relevant = [item for item in scored if _score(item, tokens) >= MIN_SCORE][:MAX_RELEVANT]
    block = render(pinned + relevant, procedures[:MAX_PROCEDURES])
    if estimate_tokens(block.message) <= max_tokens:
        return block
    # still too long: keep dropping the tail rather than truncating mid-sentence
    while relevant and estimate_tokens(render(pinned + relevant, procedures[:1]).message) > max_tokens:
        relevant = relevant[:-1]
    return render(pinned + relevant, procedures[:1] if procedures else [])


# -- extraction -------------------------------------------------------------

EXTRACT_PROMPT = """You read a finished phone-agent task and decide what, if anything,
is worth remembering about the user for future tasks.

Store a memory only if it will still be true and useful in a later, different task:
- fact: a durable fact the user told you (where they live, what they own)
- preference: how the user likes something done (always ask before ordering)
- procedure: a reusable way of doing a task the user explicitly approved

Do NOT store: what happened in this task, what is on the screen now, anything about
passwords, one-time codes or other credentials.

Answer with JSON only:
{{"memories": [{{"kind": "fact|preference|procedure", "content": "<one sentence in the user's language>", "quote": "<the user's own words, or empty>"}}]}}

If nothing is worth remembering, answer {{"memories": []}}.

Task: {task}
Outcome: {answer}"""


def extract(llm, task: str, answer: str, limit: int = 3) -> list[dict]:
    """Ask the model what is worth remembering; returns candidate dicts."""
    if llm is None or not task:
        return []
    prompt = EXTRACT_PROMPT.format(task=task[:500], answer=(answer or "")[:500])
    try:
        reply = llm.text(prompt, "Output the JSON.", temperature=0.0)
    except Exception:  # noqa: BLE001 - extraction must never break a task
        return []
    payload = _extract_json(reply)
    if not isinstance(payload, dict):
        return []
    candidates = payload.get("memories")
    if not isinstance(candidates, list):
        return []
    out = []
    for entry in candidates[:limit]:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "fact"))
        if kind not in ("fact", "preference", "procedure"):
            kind = "fact"
        content = str(entry.get("content", "")).strip()
        if content:
            out.append({"kind": kind, "content": content,
                        "quote": str(entry.get("quote", ""))[:200]})
    return out


def _extract_json(text: str):
    text = (text or "").strip()
    if "{" not in text:
        return None
    end = text.rfind("}")
    if end < 0:
        return None
    for candidate in (text, text[text.find("{"): end + 1]):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return None
