"""
report.py — Generates the HTML dashboard written to docs/index.html.

The dashboard is a self-contained single HTML file (no build step, no server).
It embeds all listing data as JSON and uses vanilla JS for client-side
filtering and sorting. Styling via Tailwind CSS CDN.
"""

import json
import re
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:,.0f}"


def _fmt_multiple(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}x"


def _score_color_class(score: float) -> str:
    if score >= 75:
        return "score-high"
    if score >= 50:
        return "score-mid"
    return "score-low"


def _score_label(score: float) -> str:
    if score >= 75:
        return "Strong"
    if score >= 50:
        return "Moderate"
    return "Weak"


def _escape(text: str) -> str:
    """HTML-escape a string for safe inline embedding."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
    )


def _build_listing_card(listing: dict) -> str:
    score = listing.get("score", 0)
    score_class = _score_color_class(score)
    score_label = _score_label(score)
    name = _escape(listing.get("name", "Unknown Business"))
    location = _escape(listing.get("location", "—"))
    category = _escape(listing.get("category", "—"))
    asking = _fmt_money(listing.get("asking_price"))
    cf = _fmt_money(listing.get("ebitda_or_sde") or listing.get("cash_flow"))
    multiple = _fmt_multiple(listing.get("ebitda_sde_multiple"))
    url = listing.get("url", "#")
    seller_fin = listing.get("seller_financing", False)
    date_added = listing.get("date_added", "")[:10]
    description = _escape((listing.get("description") or "No description available.")[:400])
    breakdown = listing.get("score_breakdown", {})

    fin_badge = (
        '<span class="badge badge-green">Seller Financing</span>'
        if seller_fin else
        '<span class="badge badge-gray">No Seller Financing</span>'
    )

    breakdown_html = ""
    if breakdown:
        items = [
            ("Valuation Multiple", breakdown.get("multiple", 0), "60%"),
            ("Cash Flow", breakdown.get("cash_flow", 0), "30%"),
            ("Seller Financing", breakdown.get("seller_financing", 0), "10%"),
        ]
        rows = "".join(
            f"""<div class="breakdown-row">
                  <span class="breakdown-label">{label} <span class="breakdown-weight">({weight})</span></span>
                  <div class="breakdown-bar-track">
                    <div class="breakdown-bar" style="width:{val}%"></div>
                  </div>
                  <span class="breakdown-val">{val:.0f}</span>
                </div>"""
            for label, val, weight in items
        )
        breakdown_html = f'<div class="breakdown">{rows}</div>'

    return f"""
    <div class="card" data-score="{score}" data-date="{date_added}" data-location="{location}" data-fin="{1 if seller_fin else 0}">
      <div class="card-header">
        <div class="card-title-group">
          <a href="{url}" target="_blank" rel="noopener" class="card-title">{name}</a>
          <div class="card-meta">
            <span class="meta-chip">{location}</span>
            <span class="meta-chip">{category}</span>
            <span class="meta-date">Added {date_added}</span>
          </div>
        </div>
        <div class="score-badge {score_class}">
          <span class="score-number">{score:.0f}</span>
          <span class="score-label">{score_label}</span>
        </div>
      </div>

      <div class="metrics-grid">
        <div class="metric">
          <span class="metric-label">Asking Price</span>
          <span class="metric-value">{asking}</span>
        </div>
        <div class="metric">
          <span class="metric-label">EBITDA / SDE</span>
          <span class="metric-value">{cf}</span>
        </div>
        <div class="metric">
          <span class="metric-label">Multiple</span>
          <span class="metric-value">{multiple}</span>
        </div>
        <div class="metric">
          <span class="metric-label">Financing</span>
          <span class="metric-value">{fin_badge}</span>
        </div>
      </div>

      {breakdown_html}

      <div class="card-description">
        <p>{description}{"..." if len(listing.get("description") or "") > 400 else ""}</p>
      </div>

      <div class="card-footer">
        <a href="{url}" target="_blank" rel="noopener" class="view-btn">
          View on BizBuySell ↗
        </a>
      </div>
    </div>"""


# ---------------------------------------------------------------------------
# Full HTML page
# ---------------------------------------------------------------------------

def generate_dashboard(
    all_listings: list[dict],
    run_timestamp: Optional[str] = None,
    run_count: int = 0,
) -> str:
    """
    Build the full HTML dashboard string.

    all_listings — all scored listings across all historical runs (newest first).
    run_timestamp — ISO string of the latest run.
    run_count — number of listings found in this run.
    """
    now_str = run_timestamp or datetime.now(timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(now_str.replace("Z", "+00:00"))
        display_time = dt.strftime("%B %d, %Y at %I:%M %p UTC")
    except Exception:
        display_time = now_str

    avg_score = (
        round(sum(l["score"] for l in all_listings) / len(all_listings), 1)
        if all_listings else 0
    )
    top_listing = all_listings[0] if all_listings else None
    top_name = _escape(top_listing.get("name", "—")) if top_listing else "—"

    cards_html = "\n".join(_build_listing_card(l) for l in all_listings) if all_listings else (
        '<div class="empty-state">No listings found yet. Run the scraper to populate results.</div>'
    )

    # Embed listing data for client-side filtering
    listings_json = json.dumps(
        [{"score": l.get("score", 0), "date": l.get("date_added", ""), "fin": l.get("seller_financing", False)}
         for l in all_listings],
        separators=(",", ":"),
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>BBS Business Scout</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    /* ── Base ──────────────────────────────────────────────── */
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f1117;
      color: #e2e8f0;
      min-height: 100vh;
    }}

    /* ── Layout ────────────────────────────────────────────── */
    .page-header {{
      background: linear-gradient(135deg, #1a1d27 0%, #12151f 100%);
      border-bottom: 1px solid #2d3148;
      padding: 24px 32px;
    }}
    .page-title {{
      font-size: 22px;
      font-weight: 700;
      color: #f1f5f9;
      letter-spacing: -0.3px;
    }}
    .page-subtitle {{
      font-size: 13px;
      color: #64748b;
      margin-top: 2px;
    }}
    .stats-bar {{
      display: flex;
      gap: 32px;
      margin-top: 16px;
      flex-wrap: wrap;
    }}
    .stat {{
      display: flex;
      flex-direction: column;
    }}
    .stat-value {{
      font-size: 20px;
      font-weight: 700;
      color: #f1f5f9;
    }}
    .stat-label {{
      font-size: 11px;
      color: #64748b;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-top: 1px;
    }}

    /* ── Controls ──────────────────────────────────────────── */
    .controls {{
      background: #13161f;
      border-bottom: 1px solid #1e2235;
      padding: 14px 32px;
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .control-label {{
      font-size: 12px;
      color: #64748b;
      margin-right: -4px;
    }}
    select, button {{
      background: #1e2235;
      border: 1px solid #2d3148;
      color: #cbd5e1;
      border-radius: 6px;
      padding: 6px 12px;
      font-size: 13px;
      cursor: pointer;
      transition: border-color 0.15s;
    }}
    select:hover, button:hover {{
      border-color: #4f6ef7;
      color: #f1f5f9;
    }}
    .results-count {{
      margin-left: auto;
      font-size: 12px;
      color: #64748b;
    }}

    /* ── Cards ─────────────────────────────────────────────── */
    .cards-container {{
      max-width: 900px;
      margin: 24px auto;
      padding: 0 24px 48px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}
    .card {{
      background: #1a1d27;
      border: 1px solid #252838;
      border-radius: 12px;
      padding: 20px 24px;
      transition: border-color 0.15s, box-shadow 0.15s;
    }}
    .card:hover {{
      border-color: #3d4568;
      box-shadow: 0 4px 24px rgba(0,0,0,0.4);
    }}
    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
    }}
    .card-title-group {{ flex: 1; min-width: 0; }}
    .card-title {{
      font-size: 17px;
      font-weight: 600;
      color: #e2e8f0;
      text-decoration: none;
      display: block;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .card-title:hover {{ color: #7c9fff; }}
    .card-meta {{
      display: flex;
      gap: 8px;
      margin-top: 6px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .meta-chip {{
      background: #252838;
      color: #94a3b8;
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 4px;
    }}
    .meta-date {{
      font-size: 11px;
      color: #475569;
    }}

    /* ── Score Badge ───────────────────────────────────────── */
    .score-badge {{
      display: flex;
      flex-direction: column;
      align-items: center;
      min-width: 58px;
      border-radius: 8px;
      padding: 8px 10px;
      flex-shrink: 0;
    }}
    .score-number {{
      font-size: 22px;
      font-weight: 800;
      line-height: 1;
    }}
    .score-label {{
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-top: 2px;
    }}
    .score-high {{ background: rgba(34,197,94,0.12); color: #4ade80; border: 1px solid rgba(34,197,94,0.25); }}
    .score-mid  {{ background: rgba(234,179,8,0.10);  color: #facc15; border: 1px solid rgba(234,179,8,0.25);  }}
    .score-low  {{ background: rgba(239,68,68,0.10);  color: #f87171; border: 1px solid rgba(239,68,68,0.20);  }}

    /* ── Metrics Grid ──────────────────────────────────────── */
    .metrics-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-top: 18px;
    }}
    @media (max-width: 600px) {{
      .metrics-grid {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    .metric {{
      background: #12151f;
      border-radius: 8px;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .metric-label {{
      font-size: 11px;
      color: #64748b;
      text-transform: uppercase;
      letter-spacing: 0.4px;
    }}
    .metric-value {{
      font-size: 16px;
      font-weight: 700;
      color: #f1f5f9;
    }}

    /* ── Badges ────────────────────────────────────────────── */
    .badge {{
      font-size: 11px;
      padding: 2px 7px;
      border-radius: 4px;
      font-weight: 600;
    }}
    .badge-green {{ background: rgba(34,197,94,0.12); color: #4ade80; }}
    .badge-gray  {{ background: #1e2235; color: #64748b; }}

    /* ── Score Breakdown ───────────────────────────────────── */
    .breakdown {{
      margin-top: 14px;
      padding: 12px 14px;
      background: #12151f;
      border-radius: 8px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .breakdown-row {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .breakdown-label {{
      font-size: 11px;
      color: #64748b;
      width: 180px;
      flex-shrink: 0;
    }}
    .breakdown-weight {{ color: #475569; }}
    .breakdown-bar-track {{
      flex: 1;
      height: 4px;
      background: #1e2235;
      border-radius: 2px;
      overflow: hidden;
    }}
    .breakdown-bar {{
      height: 100%;
      background: linear-gradient(90deg, #4f6ef7, #7c9fff);
      border-radius: 2px;
      transition: width 0.3s ease;
    }}
    .breakdown-val {{
      font-size: 11px;
      color: #94a3b8;
      width: 26px;
      text-align: right;
      flex-shrink: 0;
    }}

    /* ── Description ───────────────────────────────────────── */
    .card-description {{
      margin-top: 14px;
      font-size: 13px;
      color: #64748b;
      line-height: 1.6;
    }}

    /* ── Footer ────────────────────────────────────────────── */
    .card-footer {{
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid #1e2235;
    }}
    .view-btn {{
      font-size: 13px;
      font-weight: 500;
      color: #7c9fff;
      text-decoration: none;
    }}
    .view-btn:hover {{ color: #a5b4fc; }}

    /* ── Empty State ───────────────────────────────────────── */
    .empty-state {{
      text-align: center;
      padding: 80px 24px;
      color: #475569;
      font-size: 15px;
    }}
    .hidden {{ display: none !important; }}
  </style>
</head>
<body>

<!-- ── Header ───────────────────────────────────────────── -->
<div class="page-header">
  <div style="max-width:900px; margin:0 auto;">
    <div class="page-title">BBS Business Scout</div>
    <div class="page-subtitle">Last updated: {display_time}</div>
    <div class="stats-bar">
      <div class="stat">
        <span class="stat-value">{run_count}</span>
        <span class="stat-label">New This Run</span>
      </div>
      <div class="stat">
        <span class="stat-value">{len(all_listings)}</span>
        <span class="stat-label">Total Tracked</span>
      </div>
      <div class="stat">
        <span class="stat-value">{avg_score}</span>
        <span class="stat-label">Avg Score</span>
      </div>
      <div class="stat">
        <span class="stat-value" title="{top_name}" style="max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{top_name}</span>
        <span class="stat-label">Top Listing</span>
      </div>
    </div>
  </div>
</div>

<!-- ── Controls ─────────────────────────────────────────── -->
<div class="controls">
  <span class="control-label">Sort</span>
  <select id="sort-select" onchange="applyFilters()">
    <option value="score-desc">Score: High → Low</option>
    <option value="score-asc">Score: Low → High</option>
    <option value="date-desc">Date: Newest First</option>
    <option value="date-asc">Date: Oldest First</option>
  </select>

  <span class="control-label">Min Score</span>
  <select id="score-filter" onchange="applyFilters()">
    <option value="0">All scores</option>
    <option value="75">75+ (Strong)</option>
    <option value="50">50+ (Moderate)</option>
    <option value="25">25+</option>
  </select>

  <span class="control-label">Financing</span>
  <select id="fin-filter" onchange="applyFilters()">
    <option value="all">All</option>
    <option value="yes">Seller Financing Only</option>
  </select>

  <span class="control-label">Added</span>
  <select id="date-filter" onchange="applyFilters()">
    <option value="all">All time</option>
    <option value="7">Last 7 days</option>
    <option value="30">Last 30 days</option>
    <option value="90">Last 90 days</option>
  </select>

  <span class="results-count" id="results-count">{len(all_listings)} listings</span>
</div>

<!-- ── Cards ────────────────────────────────────────────── -->
<div class="cards-container" id="cards-container">
{cards_html}
</div>

<script>
  const listings = {listings_json};
  const cards = Array.from(document.querySelectorAll('.card'));

  function applyFilters() {{
    const sort = document.getElementById('sort-select').value;
    const minScore = parseInt(document.getElementById('score-filter').value);
    const fin = document.getElementById('fin-filter').value;
    const days = document.getElementById('date-filter').value;

    const now = new Date();
    let visible = 0;

    // Filter
    cards.forEach((card, i) => {{
      const d = listings[i];
      let show = true;

      if (d.score < minScore) show = false;
      if (fin === 'yes' && !d.fin) show = false;
      if (days !== 'all' && d.date) {{
        const added = new Date(d.date);
        const diffDays = (now - added) / (1000 * 60 * 60 * 24);
        if (diffDays > parseInt(days)) show = false;
      }}

      card.classList.toggle('hidden', !show);
      if (show) visible++;
    }});

    document.getElementById('results-count').textContent = visible + ' listing' + (visible !== 1 ? 's' : '');

    // Sort visible cards
    const container = document.getElementById('cards-container');
    const visibleCards = cards.filter((_, i) => !cards[i].classList.contains('hidden'));

    visibleCards.sort((a, b) => {{
      const ai = cards.indexOf(a), bi = cards.indexOf(b);
      const da = listings[ai], db = listings[bi];
      if (sort === 'score-desc') return db.score - da.score;
      if (sort === 'score-asc') return da.score - db.score;
      if (sort === 'date-desc') return (db.date || '').localeCompare(da.date || '');
      if (sort === 'date-asc') return (da.date || '').localeCompare(db.date || '');
      return 0;
    }});

    // Re-append in sorted order (hidden cards go to end)
    visibleCards.forEach(c => container.appendChild(c));
    cards.filter((_, i) => cards[i].classList.contains('hidden'))
         .forEach(c => container.appendChild(c));
  }}

  // Init
  applyFilters();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_dashboard(all_listings: list[dict], output_path: str, run_timestamp: str, run_count: int) -> None:
    html = generate_dashboard(all_listings, run_timestamp=run_timestamp, run_count=run_count)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
