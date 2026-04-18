"""
scraper.py — Fetches business listings from BizBuySell.

Workflow:
  1. For each configured state, paginate through search results to collect listing URLs.
  2. For each new listing URL (not in seen_ids), fetch the detail page.
  3. Parse and return structured listing data.

BizBuySell blocks plain HTTP clients; we mimic a real browser with headers and
paced requests. If selectors break after a BBS redesign, enable DEBUG_HTML=True
in run.py to dump raw HTML to data/debug/ for inspection.
"""

import re
import time
import logging
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bizbuysell.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _parse_money(text: str) -> Optional[float]:
    """Convert money strings like '$1,250,000' or '$1.2M' to float."""
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
    """Pull the numeric listing ID from a BizBuySell URL."""
    match = re.search(r"/(\d+)/?$", url.rstrip("/"))
    return match.group(1) if match else None


def _find_value_by_label(soup: BeautifulSoup, *labels: str) -> Optional[float]:
    """
    Search for a financial value on the page by scanning for label text.
    Tries multiple strategies to handle layout variations.
    """
    for label in labels:
        pattern = re.compile(rf"\b{re.escape(label)}\b", re.I)

        for node in soup.find_all(string=pattern):
            parent = node.parent
            if parent is None:
                continue

            # Strategy 1: value is in a sibling element
            sibling = parent.find_next_sibling()
            if sibling:
                val = _parse_money(sibling.get_text(strip=True))
                if val is not None:
                    return val

            # Strategy 2: value is in parent's next sibling
            grandparent = parent.parent
            if grandparent:
                uncle = grandparent.find_next_sibling()
                if uncle:
                    val = _parse_money(uncle.get_text(strip=True))
                    if val is not None:
                        return val

            # Strategy 3: label and value share a container — look for money pattern
            container = grandparent or parent
            if container:
                text = container.get_text(" ", strip=True)
                money_match = re.search(r"\$[\d,]+(?:\.\d+)?(?:\s*[KMB])?", text)
                if money_match:
                    val = _parse_money(money_match.group())
                    if val is not None:
                        return val

    return None


# ---------------------------------------------------------------------------
# Search result page scraping
# ---------------------------------------------------------------------------

def get_listing_urls_for_state(
    session: requests.Session,
    state_slug: str,
    max_pages: int = 10,
    delay: float = 2.5,
) -> list[dict]:
    """
    Paginate through BizBuySell search results for one state.
    Returns list of {id, url} dicts for all listings found.
    """
    results: list[dict] = []
    seen_ids: set[str] = set()
    listing_url_pattern = re.compile(r"/Business-Opportunity/", re.I)

    for page in range(1, max_pages + 1):
        search_url = f"{BASE_URL}/{state_slug}/businesses-for-sale/?pg={page}"
        logger.info("Fetching search page: %s", search_url)

        try:
            resp = session.get(search_url, timeout=30)
        except requests.RequestException as exc:
            logger.error("Request failed for %s: %s", search_url, exc)
            break

        if resp.status_code == 403:
            logger.warning("403 Forbidden on %s — BBS may be blocking the request.", search_url)
            break
        if resp.status_code != 200:
            logger.warning("HTTP %s on %s", resp.status_code, search_url)
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        # Collect all links that look like listing detail pages
        links = soup.find_all("a", href=listing_url_pattern)
        if not links:
            logger.info("No listing links on page %d — stopping pagination.", page)
            break

        new_count = 0
        for link in links:
            href = link.get("href", "")
            if not href:
                continue
            full_url = urljoin(BASE_URL, href)
            listing_id = _extract_listing_id(href)
            if listing_id and listing_id not in seen_ids:
                seen_ids.add(listing_id)
                results.append({"id": listing_id, "url": full_url})
                new_count += 1

        logger.info("Page %d: found %d new listing URLs (total so far: %d)", page, new_count, len(results))

        # Stop if there's no "Next" pagination link
        next_link = soup.find("a", string=re.compile(r"next", re.I)) or \
                    soup.find("a", attrs={"rel": "next"})
        if not next_link:
            logger.info("No next-page link found — end of results for %s.", state_slug)
            break

        time.sleep(delay)

    return results


# ---------------------------------------------------------------------------
# Listing detail page scraping
# ---------------------------------------------------------------------------

def get_listing_detail(
    session: requests.Session,
    listing_id: str,
    url: str,
    delay: float = 2.5,
    debug_html_dir: Optional[str] = None,
) -> Optional[dict]:
    """
    Fetch and parse a single listing detail page.
    Returns a dict with all extracted fields, or None on failure.
    """
    time.sleep(delay)
    logger.info("Fetching detail: %s", url)

    try:
        resp = session.get(url, timeout=30)
    except requests.RequestException as exc:
        logger.error("Request failed for %s: %s", url, exc)
        return None

    if resp.status_code != 200:
        logger.warning("HTTP %s for listing %s", resp.status_code, listing_id)
        return None

    if debug_html_dir:
        import os
        os.makedirs(debug_html_dir, exist_ok=True)
        with open(f"{debug_html_dir}/{listing_id}.html", "w", encoding="utf-8") as f:
            f.write(resp.text)

    soup = BeautifulSoup(resp.text, "html.parser")
    data: dict = {"id": listing_id, "url": url}

    # --- Name ---
    for selector in ["h1.bfsTitle", "h1.listing-title", "h1", "h2.bfsTitle"]:
        el = soup.select_one(selector)
        if el:
            data["name"] = el.get_text(strip=True)
            break
    data.setdefault("name", "Unknown Business")

    # --- Location ---
    for selector in [
        ".bfsRequest .city",
        ".listing-location",
        "[class*='location']",
        "[class*='Location']",
    ]:
        el = soup.select_one(selector)
        if el:
            data["location"] = el.get_text(strip=True)
            break
    if "location" not in data:
        # Fallback: grep for "City, ST" pattern in full text
        loc_match = re.search(
            r"\b([A-Z][a-zA-Z\s]+),\s*(NY|MA|CT)\b", soup.get_text()
        )
        if loc_match:
            data["location"] = loc_match.group(0)

    # --- Category / Industry ---
    for selector in [
        ".bfsRequest .category",
        "[class*='category']",
        "[class*='industry']",
        "[class*='Industry']",
    ]:
        el = soup.select_one(selector)
        if el and el.get_text(strip=True):
            data["category"] = el.get_text(strip=True)
            break

    # --- Financial fields ---
    data["asking_price"] = _find_value_by_label(soup, "Asking Price", "Listing Price")
    data["cash_flow"] = _find_value_by_label(
        soup, "Cash Flow", "Owner's Benefit", "Total Owner's Benefit", "Seller's Discretionary Earnings"
    )
    data["revenue"] = _find_value_by_label(soup, "Gross Revenue", "Gross Income", "Revenue")
    data["ebitda"] = _find_value_by_label(soup, "EBITDA", "Adjusted EBITDA")
    data["sde"] = _find_value_by_label(soup, "SDE", "Seller's Discretionary Earnings", "Owner Benefit")

    # Unify: prefer EBITDA, fall back to SDE, fall back to cash_flow
    data["ebitda_or_sde"] = data["ebitda"] or data["sde"] or data["cash_flow"]

    # --- Seller Financing ---
    full_text = soup.get_text()
    data["seller_financing"] = bool(
        re.search(r"seller\s+financ(ing|ed)", full_text, re.I)
    )

    # --- Description ---
    for selector in [
        "#listingDescription",
        ".bfsRequest .description",
        "[class*='description']",
        "[id*='description']",
        ".listing-description",
    ]:
        el = soup.select_one(selector)
        if el and len(el.get_text(strip=True)) > 50:
            data["description"] = el.get_text(" ", strip=True)[:2500]
            break

    # --- Year Established ---
    year_match = re.search(
        r"(?:established|founded|in business since|operating since)\s+(?:in\s+)?(\d{4})",
        full_text, re.I,
    )
    if year_match:
        data["year_established"] = int(year_match.group(1))

    # --- Employees ---
    emp_match = re.search(r"(\d+)\s*(?:full[- ]?time\s+)?employee", full_text, re.I)
    if emp_match:
        data["employees"] = int(emp_match.group(1))

    logger.debug("Parsed listing %s: %s", listing_id, {k: v for k, v in data.items() if k != "description"})
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_new_listings(config: dict, seen_ids: set[str]) -> list[dict]:
    """
    Main entry point for the scraper.
    Returns parsed detail dicts for listings not in seen_ids.
    """
    search_cfg = config["search"]
    delay = search_cfg.get("request_delay_seconds", 2.5)
    max_pages = search_cfg.get("max_pages_per_state", 10)

    session = _make_session()
    # Warm up the session with a homepage visit to get cookies
    try:
        session.get(BASE_URL, timeout=15)
        time.sleep(1)
    except requests.RequestException:
        pass

    all_urls: list[dict] = []
    for state_code, slug in search_cfg["state_slugs"].items():
        logger.info("Searching state: %s (%s)", state_code, slug)
        urls = get_listing_urls_for_state(session, slug, max_pages=max_pages, delay=delay)
        logger.info("Found %d total listings for %s", len(urls), state_code)
        all_urls.extend(urls)

    # Deduplicate across states
    seen_in_run: set[str] = set()
    new_urls = []
    for entry in all_urls:
        lid = entry["id"]
        if lid not in seen_ids and lid not in seen_in_run:
            seen_in_run.add(lid)
            new_urls.append(entry)

    logger.info("%d new listings to fetch (out of %d total found)", len(new_urls), len(all_urls))

    listings = []
    for entry in new_urls:
        detail = get_listing_detail(session, entry["id"], entry["url"], delay=delay)
        if detail:
            listings.append(detail)

    return listings
