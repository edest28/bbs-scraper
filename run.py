"""
run.py — Orchestrates a full scrape-score-report cycle.

Usage:
  python run.py            # normal run
  python run.py --debug    # saves raw HTML to data/debug/ for selector inspection
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scraper import scrape_new_listings
from scorer import filter_and_score
from report import write_dashboard

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = BASE_DIR / "docs"
SEEN_IDS_FILE = DATA_DIR / "seen_ids.json"
ALL_LISTINGS_FILE = DATA_DIR / "all_listings.json"
DASHBOARD_FILE = DOCS_DIR / "index.html"
CONFIG_FILE = BASE_DIR / "config.yaml"

DATA_DIR.mkdir(exist_ok=True)
DOCS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def load_seen_ids() -> set[str]:
    if SEEN_IDS_FILE.exists():
        with open(SEEN_IDS_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen_ids(seen_ids: set[str]) -> None:
    with open(SEEN_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids), f, indent=2)


def load_all_listings() -> list[dict]:
    if ALL_LISTINGS_FILE.exists():
        with open(ALL_LISTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_all_listings(listings: list[dict]) -> None:
    with open(ALL_LISTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(listings, f, indent=2, ensure_ascii=False)


def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(debug: bool = False) -> None:
    logger.info("=" * 60)
    logger.info("BBS Business Scout — starting run")
    logger.info("=" * 60)

    config = load_config()
    seen_ids = load_seen_ids()
    existing_listings = load_all_listings()

    logger.info("Loaded %d seen listing IDs, %d historical listings", len(seen_ids), len(existing_listings))

    # ── 1. Scrape ──────────────────────────────────────────────────
    debug_html_dir = str(DATA_DIR / "debug") if debug else None
    raw_listings = scrape_new_listings(config, seen_ids, debug_html_dir=debug_html_dir)
    logger.info("Scraped %d new raw listings", len(raw_listings))

    if not raw_listings:
        logger.info("No new listings found. Regenerating dashboard with existing data.")
        run_ts = datetime.now(timezone.utc).isoformat()
        write_dashboard(
            existing_listings,
            str(DASHBOARD_FILE),
            run_timestamp=run_ts,
            run_count=0,
        )
        logger.info("Dashboard written to %s", DASHBOARD_FILE)
        return

    # ── 2. Filter & Score ──────────────────────────────────────────
    scored, filtered_out = filter_and_score(raw_listings, config)
    logger.info(
        "Scored %d listings | Filtered out %d",
        len(scored), len(filtered_out),
    )

    if filtered_out:
        logger.info("Filtered out:")
        for l in filtered_out:
            logger.info("  [%s] %s — %s", l.get("id"), l.get("name", "?"), l.get("filtered_reason"))

    # ── 3. Stamp and persist ───────────────────────────────────────
    run_ts = datetime.now(timezone.utc).isoformat()
    for listing in scored:
        listing["date_added"] = run_ts[:10]  # YYYY-MM-DD

    # Add new IDs to seen set (including filtered-out, so we don't re-process them)
    new_ids = {l["id"] for l in raw_listings if l.get("id")}
    seen_ids.update(new_ids)
    save_seen_ids(seen_ids)

    # Prepend new scored listings (newest first)
    all_listings = scored + existing_listings
    save_all_listings(all_listings)

    # ── 4. Generate dashboard ──────────────────────────────────────
    write_dashboard(
        all_listings,
        str(DASHBOARD_FILE),
        run_timestamp=run_ts,
        run_count=len(scored),
    )

    logger.info("=" * 60)
    logger.info(
        "Done. %d new listings surfaced (score range: %s–%s). Dashboard: %s",
        len(scored),
        f"{scored[-1]['score']:.0f}" if scored else "—",
        f"{scored[0]['score']:.0f}" if scored else "—",
        DASHBOARD_FILE,
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BizBuySell scraper")
    parser.add_argument("--debug", action="store_true", help="Save raw HTML for selector debugging")
    args = parser.parse_args()
    main(debug=args.debug)
