"""
scorer.py — Scores and filters listings according to config.yaml rules.

Filter → Score pipeline:
  1. Hard filters: exclude anything that doesn't meet location, category,
     and minimum cash flow thresholds. Excluded listings are returned with
     a 'filtered_reason' field so we can log why they were dropped.
  2. Scoring: remaining listings get a 0–100 score from weighted factors.
  3. Enrichment: adds a human-readable score breakdown for the dashboard.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hard filter
# ---------------------------------------------------------------------------

def _location_passes(listing: dict, allowed_states: list[str]) -> bool:
    location = listing.get("location", "") or ""
    url = listing.get("url", "") or ""
    text = (location + " " + url).upper()
    return any(f",\u00a0{s}" in text or f", {s}" in text or f"/{s.lower()}" in text or f"-{s.lower()}" in text
               for s in allowed_states) or any(s.upper() in text for s in allowed_states)


def _category_excluded(listing: dict, excluded: list[str]) -> bool:
    category = (listing.get("category") or "").lower()
    name = (listing.get("name") or "").lower()
    description = (listing.get("description") or "").lower()[:200]
    combined = f"{category} {name} {description}"
    return any(term.lower() in combined for term in excluded)


def apply_filters(listing: dict, config: dict) -> Optional[str]:
    """
    Check hard filters. Returns a reason string if the listing should be
    excluded, or None if it passes.
    """
    f = config["filters"]

    # Location
    if not _location_passes(listing, f["locations"]):
        return f"location not in {f['locations']}"

    # Category blacklist
    if _category_excluded(listing, f.get("excluded_categories", [])):
        return f"excluded category ({listing.get('category', 'unknown')})"

    # Minimum cash flow (EBITDA/SDE)
    cf = listing.get("ebitda_or_sde") or listing.get("cash_flow")
    if cf is None:
        return "no cash flow / EBITDA / SDE data"
    if cf < f["min_cash_flow"]:
        return f"cash flow ${cf:,.0f} below minimum ${f['min_cash_flow']:,.0f}"

    # Asking price must exist to compute a multiple
    if not listing.get("asking_price"):
        return "no asking price"

    return None  # passes all filters


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_multiple(multiple: float, cfg: dict) -> float:
    """Score the EBITDA/SDE multiple on a 0–100 scale."""
    ideal_min = cfg["ideal_min"]
    ideal_max = cfg["ideal_max"]
    floor = cfg["floor_threshold"]
    ceiling = cfg["ceiling_threshold"]

    if multiple <= floor or multiple >= ceiling:
        return 0.0

    if ideal_min <= multiple <= ideal_max:
        # Perfect sweet spot
        return 100.0

    if multiple < ideal_min:
        # Below ideal: linear from 0 at floor → 100 at ideal_min
        return max(0.0, (multiple - floor) / (ideal_min - floor) * 100)

    # Above ideal: linear from 100 at ideal_max → 0 at ceiling
    return max(0.0, (ceiling - multiple) / (ceiling - ideal_max) * 100)


def _score_cash_flow(cash_flow: float, tiers: list[dict]) -> float:
    """Score absolute cash flow using configured tiers (top-down, first match)."""
    for tier in sorted(tiers, key=lambda t: t["min"], reverse=True):
        if cash_flow >= tier["min"]:
            return float(tier["score"])
    return 0.0


def score_listing(listing: dict, config: dict) -> dict:
    """
    Compute score for a listing that has already passed hard filters.
    Returns the listing dict enriched with score fields.
    """
    s_cfg = config["scoring"]
    weights = s_cfg["weights"]

    asking = listing["asking_price"]
    cf = listing.get("ebitda_or_sde") or listing.get("cash_flow")
    multiple = asking / cf if cf else None

    # Individual factor scores
    multiple_score = _score_multiple(multiple, s_cfg["multiple"]) if multiple else 0.0
    cf_score = _score_cash_flow(cf, s_cfg["cash_flow_tiers"]) if cf else 0.0
    financing_score = 100.0 if listing.get("seller_financing") else 0.0

    # Weighted total
    total = (
        weights["ebitda_sde_multiple"] * multiple_score
        + weights["cash_flow_absolute"] * cf_score
        + weights["seller_financing"] * financing_score
    )

    listing["score"] = round(total, 1)
    listing["score_breakdown"] = {
        "multiple": round(multiple_score, 1),
        "cash_flow": round(cf_score, 1),
        "seller_financing": round(financing_score, 1),
    }
    listing["ebitda_sde_multiple"] = round(multiple, 2) if multiple else None

    return listing


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def filter_and_score(listings: list[dict], config: dict) -> tuple[list[dict], list[dict]]:
    """
    Apply hard filters then score each listing.

    Returns:
        (scored_listings, filtered_out_listings)
        scored_listings are sorted by score descending.
    """
    scored = []
    filtered_out = []

    for listing in listings:
        reason = apply_filters(listing, config)
        if reason:
            listing["filtered_reason"] = reason
            filtered_out.append(listing)
            logger.info("Filtered out '%s': %s", listing.get("name", listing.get("id")), reason)
        else:
            score_listing(listing, config)
            scored.append(listing)
            logger.info(
                "Scored '%s': %.1f (multiple=%.1fx, cf=$%s)",
                listing.get("name", listing.get("id")),
                listing["score"],
                listing.get("ebitda_sde_multiple") or 0,
                f"{listing.get('ebitda_or_sde', 0):,.0f}",
            )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored, filtered_out
