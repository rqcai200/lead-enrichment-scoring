#!/usr/bin/env python3
"""Lead scoring TEMPLATE — fill this in with YOUR definition of a good lead.

Returns 1 (best) / 2 / 3 (weakest), or None when there isn't enough signal.
Runs locally, no API calls, free.

This is the one part of the pipeline you should NOT copy from someone else.
The weights, thresholds, and tiers below are PLACEHOLDERS marked `# TODO`.
They encode a definition of a "good lead" — yours should be different from mine.

The model has four moves, in order:
  1. un-scorable        -> None   (no signal at all)
  2. auto-qualify       -> 1      (obviously great, regardless of the blend)
  3. below the floor    -> 3      (not enough audience on any metric)
  4. weighted blend     -> 1/2/3  (everyone in between)
Then an optional activity gate adjusts a provisional 1 or 2.

Call it from the pipeline as a black box:
    score = score_lead(lead)            # lead is a normalized dict (see fields below)
"""

from __future__ import annotations
from typing import Optional


# ---------------------------------------------------------------------------
# The normalized lead dict the pipeline passes in. Map your CRM/scraper fields
# to these keys in your enrichment code, so the scorer stays CRM-agnostic.
# ---------------------------------------------------------------------------
#   lead = {
#       "linkedin_followers": int | None,
#       "twitter_followers":  int | None,   # optional secondary audience
#       "substack_subs":      int | None,   # optional secondary audience
#       "headline":           str | None,   # current title / headline
#       "titles":             list[str],    # titles from work history (recent first)
#       "company":            str | None,   # current company name
#       "country":            str | None,
#       "recent_posts":       list[dict],   # own posts, for the activity gate
#   }


# ===========================================================================
# 1. AUDIENCE THRESHOLDS  — TODO: set numbers that mean something for YOUR ICP
# ===========================================================================
# A "mega" audience auto-qualifies (score 1). The "floor" is the minimum to be
# anything better than a 3. Consider region-aware bars (a follower count means
# different things in different markets) — left as a single set here for clarity.
AUDIENCE = {
    "linkedin": {"mega": 0, "tier1": 0, "floor": 0},   # TODO e.g. 50_000 / 10_000 / 5_000
    "twitter":  {"mega": 0, "tier1": 0, "floor": 0},   # TODO
    "substack": {"mega": 0, "tier1": 0, "floor": 0},   # TODO
}

# Blend weights — must sum to 1.0. When a factor is missing (e.g. no secondary
# social), redistribute its weight across the others rather than penalizing.
WEIGHTS = {
    "linkedin": 0.40,   # TODO tune to your ICP
    "social":   0.20,   # best of twitter / substack
    "title":    0.20,
    "company":  0.20,
}


# ===========================================================================
# 2. TITLE TIER  — TODO: define seniority for YOUR audience
# ===========================================================================
# Tiny illustrative lists — replace with your own (regex works well for this).
STRONG_TITLES = ["head of", "director", "vp", "vice president", "chief", "principal"]
MID_TITLES    = ["senior", "lead", "staff"]
# Self-appointable titles are an ANTI-SIGNAL: top tier ONLY if the audience
# backs it up, otherwise weak. (Anyone can be "CEO" of a one-person shop.)
FOUNDER_TITLES = ["founder", "co-founder", "ceo", "owner", "president"]


def title_tier(lead: dict, has_tier1_audience: bool) -> int:
    text = " ".join(filter(None, [lead.get("headline"), *lead.get("titles", [])])).lower()
    if any(t in text for t in FOUNDER_TITLES):
        return 1 if has_tier1_audience else 3   # anti-signal unless audience-backed
    if any(t in text for t in STRONG_TITLES):
        return 1
    if any(t in text for t in MID_TITLES):
        return 2
    return 3


# ===========================================================================
# 3. COMPANY TIER  — TODO: bring your own list
# ===========================================================================
# Maintain your own tier-1 / tier-2 sets (or load from data/company_tiers.json,
# built from a public source like unicorn / market-cap lists). Don't hardcode
# someone else's. Only count a PAST employer if the role ended recently.
TIER1_COMPANIES: set[str] = set()   # TODO
TIER2_COMPANIES: set[str] = set()   # TODO


def company_tier(company: Optional[str]) -> int:
    if not company:
        return 3
    c = company.strip().lower()
    if c in TIER1_COMPANIES:
        return 1
    if c in TIER2_COMPANIES:
        return 2
    return 3


# ===========================================================================
# 4. AUDIENCE HELPERS
# ===========================================================================
def _audience_tier(value: Optional[int], bars: dict) -> Optional[int]:
    if not value:
        return None
    if value >= bars["tier1"]:
        return 1
    if value >= bars["floor"]:
        return 2
    return 3


def best_audience_tier(lead: dict) -> Optional[int]:
    tiers = [
        _audience_tier(lead.get("linkedin_followers"), AUDIENCE["linkedin"]),
        _audience_tier(lead.get("twitter_followers"), AUDIENCE["twitter"]),
        _audience_tier(lead.get("substack_subs"), AUDIENCE["substack"]),
    ]
    tiers = [t for t in tiers if t is not None]
    return min(tiers) if tiers else None   # best (lowest number) wins


def has_mega_audience(lead: dict) -> bool:
    return (
        (lead.get("linkedin_followers") or 0) >= AUDIENCE["linkedin"]["mega"] > 0
        or (lead.get("twitter_followers") or 0) >= AUDIENCE["twitter"]["mega"] > 0
        or (lead.get("substack_subs") or 0) >= AUDIENCE["substack"]["mega"] > 0
    )


# ===========================================================================
# THE SCORER
# ===========================================================================
def score_lead(lead: dict) -> Optional[int]:
    aud_tier = best_audience_tier(lead)
    has_company = bool(lead.get("company"))
    has_title = bool(lead.get("headline") or lead.get("titles"))

    # 1. un-scorable
    if aud_tier is None and not has_title and not has_company:
        return None

    has_tier1_audience = aud_tier == 1
    t_tier = title_tier(lead, has_tier1_audience)
    c_tier = company_tier(lead.get("company"))

    # 2. auto-qualify -> 1
    if has_mega_audience(lead):
        return 1
    if t_tier == 1 and c_tier == 1:        # TODO: your "obviously great" rule
        return 1

    # 3. below the floor on every metric -> 3
    if aud_tier == 3 or aud_tier is None:
        # No audience clears the floor. You may still want title/company to
        # rescue some leads here — that's a judgment call. Default: weak.
        base = 3
    else:
        # 4. weighted blend
        social_tier = min(
            [t for t in (
                _audience_tier(lead.get("twitter_followers"), AUDIENCE["twitter"]),
                _audience_tier(lead.get("substack_subs"), AUDIENCE["substack"]),
            ) if t is not None] or [None]
        ) if (lead.get("twitter_followers") or lead.get("substack_subs")) else None

        factors = {
            "linkedin": _audience_tier(lead.get("linkedin_followers"), AUDIENCE["linkedin"]),
            "social":   social_tier,
            "title":    t_tier,
            "company":  c_tier,
        }
        present = {k: v for k, v in factors.items() if v is not None}
        total_w = sum(WEIGHTS[k] for k in present)            # redistribute missing weight
        blended = sum(WEIGHTS[k] * v for k, v in present.items()) / total_w
        base = max(1, min(3, round(blended)))

    return apply_activity_gate(lead, base)


# ===========================================================================
# 5. ACTIVITY GATE  — TODO: set recency / frequency / engagement thresholds
# ===========================================================================
# Only adjusts a PROVISIONAL 1 or 2. A 3 is never posts-checked (don't pay to
# confirm a weak lead). Reposts must be excluded upstream — own posts only.
ACTIVE_DAYS = 30          # TODO posted within N days
ACTIVE_MIN_POSTS = 4      # TODO at least N posts in that window
ACTIVE_MIN_ENGAGEMENT = 0.005   # TODO (reactions+comments)/followers
DORMANT_DAYS = 180        # TODO no own post in N days -> dormant


def activity_tier(lead: dict) -> int:
    """1 = active, 2 = occasional, 3 = dormant. Implement against recent_posts."""
    posts = lead.get("recent_posts") or []
    if not posts:
        return 3
    # TODO: compute days-since-last-post, post count in window, avg engagement,
    # and return 1 / 2 / 3 per your thresholds above.
    raise NotImplementedError("Implement activity_tier from recent_posts")


def apply_activity_gate(lead: dict, base: int) -> int:
    if base == 3:
        return 3                      # never check a weak lead
    a = activity_tier(lead)
    if a == 1:                        # active
        return max(1, base - 1)       # can lift a 2 -> 1
    if a == 2:                        # occasional
        return min(3, base + 1) if base == 1 else base   # knock a 1 -> 2
    # dormant: cap at 3, or 2 if they have a tier-1 audience
    return 2 if best_audience_tier(lead) == 1 else 3


if __name__ == "__main__":
    # quick smoke test once you've filled in the TODOs
    demo = {
        "linkedin_followers": 12000, "headline": "Head of AI",
        "company": "Example Co", "titles": [], "recent_posts": [],
    }
    print("score:", score_lead(demo))
