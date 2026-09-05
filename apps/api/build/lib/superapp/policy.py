"""Risk-tiered action policy (Nano 2.0 Phase B — the CaMeL-lite actor gate).

The kernel answers "how much autonomy has this capability EARNED"; this
module answers "how dangerous is this action, and who asked for it". Every
autonomous act consults it before touching the world. The two compose:
policy sets the ceiling by risk and provenance, the kernel's earned level
must clear it.

Tiers:
    0  observe/log            — always allowed
    1  reversible comms to a party already in the thread — allowed for
       Nano when the trigger came from the user; when the trigger is
       CONTENT (an email, a web page), only with a clean extraction
    2  outward to new parties, standing rules, deletions — the user's own
       explicit yes, every time
    3  money and the irreversible — never autonomous, no promotion path

Provenance is who ultimately caused the action:
    "user"    — the person spoke, tapped, or typed it
    "email"   — an inbound email's content triggered it (auto-reply,
                auto-archive): the classic injection surface
    "system"  — cron/dispatcher acting on stored user intent
"""
import re
from dataclasses import dataclass

RISK_TIERS = {
    "inbox.archive_noise": 1,
    "inbox.flag_to_read": 0,
    "inbox.auto_reply": 1,
    "inbox.send_reply": 1,
    "inbox.send_new_recipient": 2,
    "inbox.delete": 2,
    "inbox.auto_reply_rule": 2,   # creating the standing rule itself
    "scout.campaign": 1,          # recurring errand on the user's own ask
    "finance.move_money": 3,
}


@dataclass
class PolicyVerdict:
    allowed: bool
    tier: int
    reason: str


def assess(action_key: str, *, provenance: str,
           suspicious: bool = False) -> PolicyVerdict:
    """May Nano do this on its own, right now? Unknown actions default to
    tier 2: nothing new becomes autonomous by omission."""
    tier = RISK_TIERS.get(action_key, 2)
    if tier >= 3:
        return PolicyVerdict(False, tier, "hard-capped: never autonomous")
    if tier == 2:
        if provenance == "user":
            return PolicyVerdict(True, tier, "the user asked directly")
        return PolicyVerdict(False, tier, "tier 2 needs the user's own yes")
    if provenance == "email":
        if suspicious:
            return PolicyVerdict(False, tier,
                                 "the triggering email tried to steer the assistant")
        return PolicyVerdict(True, tier, "clean extraction, reply-to-sender only")
    return PolicyVerdict(True, tier, f"tier {tier}, {provenance} provenance")


_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)
_ADDR_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_BARE_DOMAIN_RE = re.compile(
    r"\b[a-z0-9][a-z0-9-]{1,60}\.(?:com|net|org|io|co|me|app|dev|xyz|info"
    r"|biz|us|uk|in|ai|gg|link|site|online)\b", re.I)
_SPELLED_RE = re.compile(
    r"\b[\w-]{2,}\s*(?:\(|\[)?\s*(?:dot|d0t)\s*(?:\)|\])?\s*"
    r"(?:com|net|org|io|co|me)\b", re.I)

_INJECTION_RE = re.compile(
    r"(ignore (?:all |any )?(?:previous|prior|above) (?:instructions?|prompts?)"
    r"|disregard (?:your|the) (?:instructions?|system prompt)"
    r"|you are (?:now |an? )?(?:ai|assistant|agent)\b.{0,40}\b(?:must|should|will)"
    r"|(?:assistant|ai|agent|bot)[,:]? (?:please )?(?:send|forward|reply|pay|transfer|delete)"
    r"|do not (?:tell|inform|alert) (?:the |your )?(?:user|owner|human))",
    re.I)


def looks_like_injection(text: str) -> bool:
    """Cheap tripwire for content that addresses the assistant instead of the
    person. The triage model makes the real call; this backstops stub mode
    and obvious cases."""
    return bool(_INJECTION_RE.search(text or ""))


def _norm(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKC", text or "").casefold()


def draft_leaks_new_destination(draft: str, source: str,
                                allowed: str = "") -> bool:
    """CaMeL-lite exfiltration check: an auto-sendable draft may not carry a
    URL, email address, bare domain, or spelled-out domain ('evil dot com')
    that the original message (the only untrusted input the drafter saw) did
    not itself contain — that is how an injected email smuggles a payload
    out. Such a draft waits for the user's tap instead. `allowed` carries
    legitimately-known strings (the sender's address, the user's own) so
    signatures don't false-positive. Not airtight — the suspicious flag is
    the primary gate; this is the backstop."""
    d = _norm(draft)
    src = _norm(source) + " " + _norm(allowed)
    for url in _URL_RE.findall(d):
        if url.rstrip(".,;/") not in src:
            return True
    for addr in _ADDR_RE.findall(d):
        if addr not in src:
            return True
    for m in _BARE_DOMAIN_RE.finditer(d):
        if m.group(0) not in src:
            return True
    if _SPELLED_RE.search(d):
        return True  # nobody spells out a domain in a genuine reply
    return False
