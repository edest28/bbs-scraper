"""
scraper.py — Fetches business listings from BizBuySell via ScraperAPI.

BizBuySell uses Akamai bot protection that blocks headless browsers and
plain HTTP clients. ScraperAPI routes requests through residential proxies
that Akamai cannot distinguish from real users.

Sign up for a free key (5,000 req/month — enough for daily runs) at:
  https://www.scraperapi.com

Set it as:
  - GitHub secret: SCRAPER_API_KEY (Actions tab → Secrets)
  - Local: export SCRAPER_API_KEY=your_key  (or add to .env)
"""

import os
import re
import time
import logging
from typing import Optional
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bizbuysell.com"
SCRAPER_API_ENDPOINT = "https://api.scraperapi.com/"
LISTING_ID_RE = re.compile(r"/(\d+)/?$")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

def _get_api_key() -> str:
    key = os.getenv("SCRAPER_API_KEY", "").strip()
    if not key:
        raise EnvironmentError(
            "SCRAPER_API_KEY is not set.\n"
            "Sign up free at https://www.scraperapi.com, then:\n"
            "  export SCRAPER_API_KEY=your_key   (local)\n"
            "  Add as GitHub secret SCRAPER_API_KEY  (Actions)"
        )
    return key


def _fetch(url: str, session: requests.Session, retries: int = 2) -> Optional[str]:
    """Fetch a URL through ScraperAPI, returning HTML text or None."""
    api_key = _get_api_key()
    params = {
        "api_key": api_key,
        "url": url,
        "render": "false",   # JS rendering costs 5× credits; BBS is server-rendered
        "keep_headers": "false",
    }
    api_url = f"{SCRAPER_API_ENDPOINT}?{urlencode(params)}"

    for attempt in range(1, retries + 2):
        try:
            resp = session.get(api_url, timeout=60)
            if resp.status_code == 200:
                return resp.text
            logger.warning("ScraperAPI returned %s for %s (attempt %d)", resp.status_code, url, attempt)
        except requests.RequestException as exc:
            logger.warning("Request error for %s (attempt %d): %s", url, attempt, exc)

        if attempt <= retries:
            time.sleep(3 * attempt)

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_money(text: str) -> Optional[float]:
    if not text:
        return None
    text = text.strip()
    if text in ("N/A", "--", "Not Disclosed", "Undisclosed", ""):
        return None

    multiplier = 1.0
    upper = text.upper()
    if "B" in upper:
        multiplier = 1_000_000_000
        text = re.sub(r"[Bb]", "", text)
    elif "M" in upper:
        multiplier = 1_000_000
        text = re.sub(r"[Mm]", "", text)
    elif "K" in upper:
        multiplier = 1_000
        text = re.sub(r"[Kk]", "", text)

    text = re.sub(r"[^\d.]", "", text)
    try:
        return float(text) * multiplier
    except (ValueError, TypeError):
        return None


def _extract_listing_id(url: str) -> Optional[str]:
    match = LISTING_ID_RE.search(url.rstrip("/"))
    return match.group(1) if match else None


def _find_value_by_label(soup: BeautifulSoup, *labels: str) -> Optional[float]:
    """Find a financial value by scanning for its label text."""
    for label in labels:
        pattern = re.compile(rf"\b{re.escape(label)}\b", re.I)

        for node in soup.find_all(string=pattern):
            parent = node.parent
            if not parent:
                continue

            # Strategy 1: value in next sibling element
            sibling = parent.find_next_sibling()
            if sibling:
                val = _parse_money(sibling.get_text(strip=True))
                if val is not None:
                    return val

            # Strategy 2: value in grandparent's next sibling
            grandparent = parent.parent
            if grandparent:
                uncle = grandparent.find_next_sibling()
                if uncle:
                    val = _parse_money(uncle.get_text(strip=True))
                    if val is not None:
                        return val

        # Strategy 3: regex on full page text for "Label: $X"
        body = soup.get_text(" ", strip=True)
        match = re.search(
            rf"\b{re.escape(label)}\b[:\s]*(\$[\d,]+(?:\.\d+)?(?:\s*[KMB])?)",
            body, re.I,
        )
        if match:
            val = _parse_money(match.group(1))
            if val is not None:
                return val

    return None


# ---------------------------------------------------------------------------
# Search result page scraping
# ---------------------------------------------------------------------------

def _get_listing_urls_for_state(
    session: requests.Session,
    state_slug: str,
    max_pages: int,
    delay: float,
) -> list[dict]:
    results: list[dict] = []
    seen_ids: set[str] = set()

    for page_num in range(1, max_pages + 1):
        url = f"{BASE_URL}/{state_slug}/businesses-for-sale/?pg={page_num}"
        logger.info("Fetching search page: %s", url)

        html = _fetch(url, session)
        if not html:
            logger.warning("No HTML returned for %s — stopping.", url)
            break

        soup = BeautifulSoup(html, "lxml")

        # Collect all hrefs that look like BizBuySell listing detail URLs
        links = soup.find_all("a", href=re.compile(r"/Business-Opportunity/", re.I))
        if not links:
            logger.info("No listing links on page %d — stopping pagination for %s.", page_num, state_slug)
            break

        new_count = 0
        for link in links:
            href = link.get("href", "")
            if not href:
                continue
            full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
            listing_id = _extract_listing_id(href)
            if listing_id and listing_id not in seen_ids:
                seen_ids.add(listing_id)
                results.append({"id": listing_id, "url": full_url})
                new_count += 1

        logger.info("Page %d: %d new listing URLs (state total: %d)", page_num, new_count, len(results))

        # Stop if no next-page link
        if not soup.find("a", string=re.compile(r"next", re.I)) and \
           not soup.find("a", attrs={"rel": "next"}):
            logger.info("No next-page link — end of results for %s.", state_slug)
            break

        time.sleep(delay)

    return results


# ---------------------------------------------------------------------------
# Listing detail page scraping
# ---------------------------------------------------------------------------

def _get_listing_detail(
    session: requests.Session,
    listing_id: str,
    url: str,
    delay: float,
    debug_html_dir: Optional[str],
) -> Optional[dict]:
    time.sleep(delay)
    logger.info("Fetching detail: %s", url)

    html = _fetch(url, session)
    if not html:
        return None

    if debug_html_dir:
        import os as _os
        _os.makedirs(debug_html_dir, exist_ok=True)
        with open(f"{debug_html_dir}/{listing_id}.html", "w", encoding="utf-8") as f:
            f.write(html)

    soup = BeautifulSoup(html, "lxml")
    data: dict = {"id": listing_id, "url": url}
    full_text = soup.get_text(" ", strip=True)

    # --- Name ---
    for selector in ["h1.bfsTitle", "h1.listing-title", "h1", "h2.bfsTitle"]:
        el = soup.select_one(selector)
        if el and el.get_text(strip=True):
            data["name"] = el.get_text(strip=True)
            break
    data.setdefault("name", "Unknown Business")

    # --- Location ---
    for selector in [".bfsRequest .city", ".listing-location", "[class*='location']"]:
        el = soup.select_one(selector)
        if el and el.get_text(strip=True):
            data["location"] = el.get_text(strip=True)
            break
    if "location" not in data:
        loc_match = re.search(r"\b([A-Z][a-zA-Z\s]+),\s*(NY|MA|CT)\b", full_text)
        if loc_match:
            data["location"] = loc_match.group(0)

    # --- Category ---
    for selector in [".bfsRequest .category", "[class*='category']", "[class*='industry']"]:
        el = soup.select_one(selector)
        if el and el.get_text(strip=True):
            data["category"] = el.get_text(strip=True)
            break

    # --- Financial fields ---
    data["asking_price"]  = _find_value_by_label(soup, "Asking Price", "Listing Price")
    data["cash_flow"]     = _find_value_by_label(soup, "Cash Flow", "Owner's Benefit", "Total Owner's Benefit")
    data["revenue"]       = _find_value_by_label(soup, "Gross Revenue", "Gross Income", "Revenue")
    data["ebitda"]        = _find_value_by_label(soup, "EBITDA", "Adjusted EBITDA")
    data["sde"]           = _find_value_by_label(soup, "SDE", "Seller's Discretionary Earnings", "Owner Benefit")
    data["ebitda_or_sde"] = data["ebitda"] or data["sde"] or data["cash_flow"]

    # --- Seller Financing ---
    data["seller_financing"] = bool(re.search(r"seller\s+financ(ing|ed)", full_text, re.I))

    # --- Description ---
    for selector in ["#listingDescription", ".bfsRequest .description", "[class*='description']"]:
        el = soup.select_one(selector)
        if el and len(el.get_text(strip=True)) > 50:
            data["description"] = el.get_text(" ", strip=True)[:2500]
            break

    # --- Year Established ---
    year_match = re.search(
        r"(?:established|founded|in business since)\s+(?:in\s+)?(\d{4})", full_text, re.I
    )
    if year_match:
        data["year_established"] = int(year_match.group(1))

    # --- Employees ---
    emp_match = re.search(r"(\d+)\s*(?:full[- ]?time\s+)?employee", full_text, re.I)
    if emp_match:
        data["employees"] = int(emp_match.group(1))

    logger.debug("Parsed %s: %s", listing_id, {k: v for k, v in data.items() if k != "description"})
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_new_listings(
    config: dict,
    seen_ids: set[str],
    debug_html_dir: Optional[str] = None,
) -> list[dict]:
    """Main entry point. Returns parsed listings not in seen_ids."""
    search_cfg = config["search"]
    delay = search_cfg.get("request_delay_seconds", 2.5)
    max_pages = search_cfg.get("max_pages_per_state", 10)

    session = requests.Session()
    session.headers.update(HEADERS)

    all_urls: list[dict] = []
    for state_code, slug in search_cfg["state_slugs"].items():
        logger.info("Searching state: %s (%s)", state_code, slug)
        urls = _get_listing_urls_for_state(session, slug, max_pages=max_pages, delay=delay)
        logger.info("Found %d total listings for %s", len(urls), state_code)
        all_urls.extend(urls)

    seen_in_run: set[str] = set()
    new_urls = [
        e for e in all_urls
        if e["id"] not in seen_ids and e["id"] not in seen_in_run
        and not seen_in_run.add(e["id"])  # type: ignore[func-returns-value]
    ]

    logger.info("%d new listings to fetch (out of %d total found)", len(new_urls), len(all_urls))

    listings = []
    for entry in new_urls:
        detail = _get_listing_detail(
            session, entry["id"], entry["url"], delay=delay, debug_html_dir=debug_html_dir
        )
        if detail:
            listings.append(detail)

    return listings
