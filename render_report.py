#!/usr/bin/env python3
"""
render_report.py v2.0
═══════════════════════════════════════════════════════════════════
Economic Intelligence System — Layer 3: Presentation
═══════════════════════════════════════════════════════════════════
Reads  : data/raw_YYYY-MM-DD.json  (produced by fetch_data.py)
Outputs: charts/chart-1_equity_YYYY-MM-DD.png
         charts/chart-2_rates_YYYY-MM-DD.png
         charts/chart-3_heatmap_YYYY-MM-DD.png
         reports/econ-insight_YYYY-MM-DD.md
         reports/econ-insight_YYYY-MM-DD.pdf

Usage:
  python render_report.py                         # uses today's JSON
  python render_report.py data/raw_2026-06-14.json  # specific file
═══════════════════════════════════════════════════════════════════
"""

import sys, json, re, math
from pathlib import Path
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch

# ─── Paths ───────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
CHARTS_DIR  = BASE_DIR / "charts"
for d in [REPORTS_DIR, CHARTS_DIR]:
    d.mkdir(exist_ok=True)

# ─── Theme ───────────────────────────────────────────────────────
BG     = "#0d1117"
PANEL  = "#161b22"
GRID   = "#21262d"
TEXT   = "#c9d1d9"
SUB    = "#8b949e"
ACCENT = ["#58a6ff","#f78166","#3fb950","#d2a8ff","#ffa657","#79c0ff"]
POS    = "#3fb950"
NEG    = "#f78166"

# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────
def parse_pct(s):
    """Extract first number from strings like '+1.23%', '-45 bps', '[N/A...]'. Returns float or None."""
    if not isinstance(s, str) or "N/A" in s or "failed" in s.lower():
        return None
    m = re.search(r"([-+]?\d+\.?\d*)", s)
    return float(m.group()) if m else None

def fmt_val(v, decimals=2):
    if v is None: return "N/A"
    if abs(v) >= 1000: return f"{v:,.{decimals}f}"
    return f"{v:.{decimals}f}"

def stale_flag(entry):
    return " ⚠" if entry.get("freshness", {}).get("is_stale") else ""

def get_history(entry):
    hist = entry.get("history", [])
    if not hist: return [], []
    dates = [datetime.strptime(h["date"], "%Y-%m-%d") for h in hist]
    vals  = [h["value"] for h in hist]
    return dates, vals

def style_ax(ax, title="", ylabel=""):
    ax.set_facecolor(PANEL)
    ax.set_title(title, color=TEXT, fontsize=10, fontweight="bold", pad=8)
    ax.set_ylabel(ylabel, color=SUB, fontsize=8)
    ax.grid(True, color=GRID, linewidth=0.5)
    for s in ax.spines.values(): s.set_color(GRID)
    ax.tick_params(colors=SUB, labelsize=7.5)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")


# ─────────────────────────────────────────────────────────────────
# CHART 1 — Equity 30-day normalized
# ─────────────────────────────────────────────────────────────────
def chart_equity(data, today, footer):
    fig, ax = plt.subplots(figsize=(10, 4.5), facecolor=BG)
    style_ax(ax, "Major Equity Indices — 30-Day Trend (Base 100)", "Indexed")

    targets = {"^GSPC":"S&P 500","^NDX":"NASDAQ 100","^N225":"Nikkei 225","^HSI":"Hang Seng"}
    plotted = 0
    for i,(tk,name) in enumerate(targets.items()):
        entry = data.get("market_data",{}).get(tk,{})
        dates, vals = get_history(entry)
        if len(vals) >= 5:
            d30, v30 = dates[-30:], vals[-30:]
            norm = [v/v30[0]*100 for v in v30]
            ax.plot(d30, norm, color=ACCENT[i], lw=1.8, label=name, zorder=3)
            # Annotate last value
            ax.annotate(f"{norm[-1]:.1f}", xy=(d30[-1], norm[-1]),
                        color=ACCENT[i], fontsize=7, va="center",
                        xytext=(6,0), textcoords="offset points")
            plotted += 1

    if plotted:
        ax.axhline(100, color=TEXT, lw=0.6, ls="--", alpha=0.3, zorder=1)
        ax.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=8,
                  loc="upper left", framealpha=0.9)
    else:
        ax.text(0.5, 0.5, "No 30-day history in JSON.\nRe-run fetch_data.py v1.1+ to populate.",
                ha="center", va="center", color=SUB, fontsize=10, transform=ax.transAxes)

    fig.text(0.99, 0.01, footer, ha="right", color=SUB, fontsize=6)
    fig.patch.set_facecolor(BG)
    path = CHARTS_DIR / f"chart-1_equity_{today}.png"
    fig.tight_layout(pad=1.2)
    fig.savefig(path, dpi=140, facecolor=BG)
    plt.close(fig)
    return path, plotted > 0


# ─────────────────────────────────────────────────────────────────
# CHART 2 — Rates & yields
# ─────────────────────────────────────────────────────────────────
def chart_rates(data, today, footer):
    fig, ax = plt.subplots(figsize=(10, 4.5), facecolor=BG)
    style_ax(ax, "US Rates & Treasury Yields — 30-Day Trend", "Rate (%)")

    targets = {"DFF":"Fed Funds Rate","DGS10":"10Y Treasury","DGS2":"2Y Treasury"}
    plotted = 0
    for i,(sid,name) in enumerate(targets.items()):
        entry = data.get("fred_data",{}).get(sid,{})
        dates, vals = get_history(entry)
        if len(vals) >= 2:
            d30, v30 = dates[-30:], vals[-30:]
            ax.plot(d30, v30, color=ACCENT[i], lw=1.8, label=name,
                    marker="o", ms=2.5, zorder=3)
            ax.annotate(f"{v30[-1]:.2f}%", xy=(d30[-1], v30[-1]),
                        color=ACCENT[i], fontsize=7, va="center",
                        xytext=(6,0), textcoords="offset points")
            plotted += 1

    if plotted:
        ax.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=8, framealpha=0.9)
        d10 = data.get("fred_data",{}).get("DGS10",{}).get("value")
        d2  = data.get("fred_data",{}).get("DGS2",{}).get("value")
        if d10 and d2:
            spread = (d10-d2)*100
            color  = POS if spread > 0 else NEG
            ax.text(0.02, 0.96,
                    f"10Y–2Y Spread: {spread:+.0f} bps  ({'Normal' if spread>0 else 'Inverted'})",
                    transform=ax.transAxes, color=color, fontsize=8.5, fontweight="bold",
                    va="top",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor=PANEL, edgecolor=color, alpha=0.9))
    else:
        ax.text(0.5, 0.5, "No FRED rate history.\nSet FRED_API_KEY in .env and re-run fetch_data.py.",
                ha="center", va="center", color=SUB, fontsize=10, transform=ax.transAxes)

    fig.text(0.99, 0.01, footer, ha="right", color=SUB, fontsize=6)
    fig.patch.set_facecolor(BG)
    path = CHARTS_DIR / f"chart-2_rates_{today}.png"
    fig.tight_layout(pad=1.2)
    fig.savefig(path, dpi=140, facecolor=BG)
    plt.close(fig)
    return path, plotted > 0


# ─────────────────────────────────────────────────────────────────
# CHART 3 — Cross-asset heatmap
# ─────────────────────────────────────────────────────────────────
def chart_heatmap(data, today, footer):
    market = data.get("market_data", {})
    rows, labels, categories = [], [], []

    DISPLAY_ORDER = [
        "^GSPC","^NDX","^N225","^HSI",     # equities
        "DX-Y.NYB","USDJPY=X","EURUSD=X",  # FX
        "GC=F","CL=F",                      # commodities
        "BTC-USD","ETH-USD",                # crypto
    ]

    cols = ["change_1d","change_1w","change_ytd"]
    col_labels = ["1D","1W","YTD"]

    for tk in DISPLAY_ORDER:
        entry = market.get(tk,{})
        if "label" not in entry: continue
        vals = [parse_pct(entry.get(c)) for c in cols]
        rows.append(vals)
        labels.append(entry["label"])
        categories.append(entry.get("category",""))

    n = len(rows)
    if n == 0:
        fig, ax = plt.subplots(figsize=(6,3), facecolor=BG)
        ax.set_facecolor(PANEL)
        ax.text(0.5,0.5,"No data available", ha="center", va="center",
                color=SUB, fontsize=11, transform=ax.transAxes)
        ax.axis("off")
        path = CHARTS_DIR / f"chart-3_heatmap_{today}.png"
        fig.savefig(path, dpi=140, facecolor=BG)
        plt.close(fig)
        return path, False

    arr = np.array([[np.nan if v is None else v for v in r] for r in rows])
    fig, ax = plt.subplots(figsize=(6.5, 0.45*n + 1.2), facecolor=BG)
    ax.set_facecolor(PANEL)

    has_data = not np.all(np.isnan(arr))
    if has_data:
        vmax = max(np.nanmax(np.abs(arr)), 1)
        im = ax.imshow(arr, cmap="RdYlGn", vmin=-vmax, vmax=vmax, aspect="auto", alpha=0.85)

    for i in range(n):
        for j in range(3):
            v = arr[i,j]
            if np.isnan(v):
                ax.add_patch(FancyBboxPatch((j-0.48, i-0.45), 0.96, 0.9,
                             boxstyle="round,pad=0.02",
                             facecolor="#21262d", edgecolor="none", zorder=2))
                ax.text(j, i, "N/A", ha="center", va="center",
                        color="#444c56", fontsize=7, fontweight="bold", zorder=3)
            else:
                sign = "+" if v > 0 else ""
                ax.text(j, i, f"{sign}{v:.1f}%", ha="center", va="center",
                        color="#0d1117" if abs(v) > vmax*0.3 else TEXT,
                        fontsize=8, fontweight="bold", zorder=3)

    ax.set_xticks(range(3))
    ax.set_xticklabels(col_labels, color=TEXT, fontsize=9, fontweight="bold")
    ax.set_yticks(range(n))
    ax.set_yticklabels(labels, color=TEXT, fontsize=8)
    ax.tick_params(length=0)
    ax.xaxis.tick_top()

    # Category separators
    cat_breaks = []
    for i in range(1,n):
        if categories[i] != categories[i-1]:
            cat_breaks.append(i-0.5)
    for b in cat_breaks:
        ax.axhline(b, color=GRID, lw=1.5, zorder=4)

    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(n-0.5, -0.5)
    ax.set_title("Cross-Asset Performance Heatmap", color=TEXT, fontsize=10,
                 fontweight="bold", pad=12)
    for s in ax.spines.values(): s.set_color(GRID)

    if not has_data:
        ax.text(0.5, 0.5, "Live 1D changes unavailable\n(Yahoo Finance rate-limited — see Section 6)",
                ha="center", va="center", color=SUB, fontsize=9,
                transform=ax.transAxes, zorder=5)

    fig.text(0.99, 0.01, footer, ha="right", color=SUB, fontsize=6)
    fig.patch.set_facecolor(BG)
    path = CHARTS_DIR / f"chart-3_heatmap_{today}.png"
    fig.tight_layout(pad=1.0)
    fig.savefig(path, dpi=140, facecolor=BG)
    plt.close(fig)
    return path, has_data


# ─────────────────────────────────────────────────────────────────
# AUTO NARRATIVE — Data-driven, no LLM needed
# ─────────────────────────────────────────────────────────────────
def auto_narrative(data):
    mkt  = data.get("market_data", {})
    fred = data.get("fred_data", {})

    # ── Risk regime ─────────────────────────────────────────────
    spx_v   = mkt.get("^GSPC", {}).get("value")
    spx_1d  = parse_pct(mkt.get("^GSPC", {}).get("change_1d"))
    ndx_1d  = parse_pct(mkt.get("^NDX",  {}).get("change_1d"))
    n225_1d = parse_pct(mkt.get("^N225", {}).get("change_1d"))

    dff   = fred.get("DFF",   {}).get("value")
    dgs10 = fred.get("DGS10", {}).get("value")
    dgs2  = fred.get("DGS2",  {}).get("value")

    btc_v  = mkt.get("BTC-USD",   {}).get("value")
    gold_v = mkt.get("GC=F",      {}).get("value")
    wti_v  = mkt.get("CL=F",      {}).get("value")
    dxy_v  = mkt.get("DX-Y.NYB",  {}).get("value")
    jpyusd = mkt.get("USDJPY=X",  {}).get("value")

    paras = []
    data_available = any(v is not None for v in [spx_1d, dff, dgs10])

    if not data_available:
        return (
            "**Data Note:** Live market and rate data were unavailable for this session. "
            "All values shown in the dashboard reflect the prior session's close (Tier 3 fallback). "
            "No intraday narrative can be generated. Re-run fetch_data.py during market hours "
            "to obtain live data.\n\n"
            "See Section 6 for a full list of data quality flags."
        )

    # Paragraph 1 — Equities & regime
    if spx_1d is not None:
        regime = ("Risk-On" if spx_1d > 0.3 else
                  "Risk-Off" if spx_1d < -0.3 else "Transitional")
        move   = "advanced" if spx_1d > 0 else "pulled back"
        p1 = f"**Risk Regime: {regime}.** US equities {move} on the session: "
        p1 += f"S&P 500 {spx_1d:+.2f}%"
        if ndx_1d is not None:
            diff = ndx_1d - spx_1d
            if   diff >  0.5: p1 += f", NASDAQ 100 outperformed at {ndx_1d:+.2f}% (tech leading)"
            elif diff < -0.5: p1 += f", NASDAQ 100 lagged at {ndx_1d:+.2f}% (value/defensive rotation)"
            else:             p1 += f", NASDAQ 100 {ndx_1d:+.2f}% (in-line with broader market)"
        if n225_1d is not None:
            p1 += f". Nikkei 225 {n225_1d:+.2f}% in Tokyo"
        p1 += "."
    else:
        p1 = "Intraday equity moves unavailable — values reflect prior session close."
    paras.append(p1)

    # Paragraph 2 — Rates & yield curve
    if dff and dgs10 and dgs2:
        spread = (dgs10 - dgs2) * 100
        state  = ("steepening — markets pricing in longer-term growth" if spread > 50
                  else "mildly positive — limited inversion risk near-term" if spread > 0
                  else "mildly inverted — growth concerns remain" if spread > -50
                  else "deeply inverted — historical recession signal; watch for Fed pivot")
        p2  = f"The Fed Funds rate stands at {dff:.2f}%, with the 10Y Treasury yield at {dgs10:.2f}% "
        p2 += f"and the 2Y at {dgs2:.2f}%. "
        p2 += f"The 10Y–2Y spread is {spread:+.0f} bps ({state})."
        if fred.get("ECBDFR",{}).get("value"):
            p2 += f" The ECB deposit rate is {fred['ECBDFR']['value']:.2f}%."
        paras.append(p2)
    elif dff:
        paras.append(f"The Fed Funds rate is at {dff:.2f}%. Treasury yield data unavailable this run.")

    # Paragraph 3 — FX, commodities, crypto
    parts = []
    if dxy_v:  parts.append(f"DXY Dollar Index at {dxy_v:.2f}")
    if jpyusd: parts.append(f"USD/JPY at {jpyusd:.2f} (JPY {'weak' if jpyusd > 145 else 'stable'} vs dollar)")
    if gold_v: parts.append(f"Gold at ${gold_v:,.0f}/oz")
    if wti_v:  parts.append(f"WTI Crude at ${wti_v:.2f}/bbl")
    if btc_v:
        btc_1d = parse_pct(mkt.get("BTC-USD",{}).get("change_1d"))
        btc_txt = f"Bitcoin at ${btc_v:,.0f}"
        if btc_1d: btc_txt += f" ({btc_1d:+.2f}% on session)"
        parts.append(btc_txt)
    if parts:
        paras.append(". ".join(parts) + ".")

    # Data caveat if fallback
    any_fallback = any(
        "fallback" in str(e.get("source","")).lower()
        for e in mkt.values() if isinstance(e, dict)
    )
    if any_fallback:
        paras.append(
            "*Note: Market prices sourced from prior session (Yahoo Finance rate-limited). "
            "1D change figures are unavailable. Values reflect last known close.*"
        )

    return "\n\n".join(paras)


# ─────────────────────────────────────────────────────────────────
# EXECUTIVE SUMMARY
# ─────────────────────────────────────────────────────────────────
def auto_summary(data):
    mkt  = data.get("market_data", {})
    fred = data.get("fred_data", {})
    bullets = []

    # Equity move
    spx_1d = parse_pct(mkt.get("^GSPC",{}).get("change_1d"))
    if spx_1d is not None:
        direction = "rose" if spx_1d > 0 else "fell"
        bullets.append(f"S&P 500 {direction} {abs(spx_1d):.2f}% on the session.")
    else:
        spx_v = mkt.get("^GSPC",{}).get("value")
        ytd   = mkt.get("^GSPC",{}).get("change_ytd","N/A")
        if spx_v:
            bullets.append(f"S&P 500 last at {spx_v:,.0f} (YTD: {ytd}). Live session data unavailable.")

    # Yield curve
    dgs10 = fred.get("DGS10",{}).get("value")
    dgs2  = fred.get("DGS2",{}).get("value")
    if dgs10 and dgs2:
        spread = (dgs10-dgs2)*100
        status = "positive" if spread > 0 else "inverted"
        bullets.append(f"10Y–2Y yield spread is {spread:+.0f} bps ({status}). Fed Funds: {fred.get('DFF',{}).get('value','N/A'):.2f}%.")

    # Gold or BTC
    gold_v = mkt.get("GC=F",{}).get("value")
    btc_v  = mkt.get("BTC-USD",{}).get("value")
    if gold_v:
        gold_ytd = mkt.get("GC=F",{}).get("change_ytd","N/A")
        bullets.append(f"Gold at ${gold_v:,.0f}/oz (YTD: {gold_ytd}).")
    elif btc_v:
        bullets.append(f"Bitcoin at ${btc_v:,.0f}.")

    if not bullets:
        bullets.append("Insufficient live data — see Section 6 for quality flags.")

    return bullets[:3]


# ─────────────────────────────────────────────────────────────────
# RISK REGIME
# ─────────────────────────────────────────────────────────────────
def auto_regime(data):
    spx = parse_pct(data.get("market_data",{}).get("^GSPC",{}).get("change_1d"))
    if spx is None: return "Undetermined (no live data)"
    if spx >  0.3: return "Risk-On"
    if spx < -0.3: return "Risk-Off"
    return "Transitional"



# ─────────────────────────────────────────────────────────────────
# CHART ANALYSIS — data-driven text for each chart
# ─────────────────────────────────────────────────────────────────
def chart_analysis_equity(data, fname):
    mkt = data.get("market_data", {})
    targets = {"^GSPC":"S&P 500","^NDX":"NASDAQ 100","^N225":"Nikkei 225","^HSI":"Hang Seng"}

    # Compute 30-day % for each from history
    perf = {}
    for tk, name in targets.items():
        hist = mkt.get(tk, {}).get("history", [])
        if len(hist) >= 5:
            base = hist[max(0, len(hist)-30)]["value"]
            end  = hist[-1]["value"]
            perf[name] = (end/base - 1)*100

    best  = max(perf, key=perf.get) if perf else None
    worst = min(perf, key=perf.get) if perf else None

    lines = [f"### 📊 Chart 1: Major Equity Indices — 30-Day Performance\n",
             f"![Chart 1]({fname})\n",
             "**What this chart shows:**  ",
             "Each index is normalized to 100 at the start of the 30-day window so you can "
             "compare *relative performance*, not absolute price. A line above 100 means the "
             "index is up since the period start. Diverging lines signal de-correlation — "
             "driven by different regional or sector factors.\n",
             "**Key Observations:**"]

    if perf:
        for name, pct in sorted(perf.items(), key=lambda x: -x[1]):
            trend = "↑ outperforming" if pct > 0 else "↓ underperforming"
            lines.append(f"- **{name}:** {pct:+.1f}% over 30 days ({trend})")
        if best and worst and best != worst:
            gap = perf[best] - perf[worst]
            lines.append(f"- **Divergence:** {best} leads {worst} by {gap:.1f} pts — "
                        f"{'significant de-correlation; regional/sector factors diverging' if gap > 5 else 'markets broadly tracking each other'}")
    else:
        lines.append("- 30-day history unavailable — re-run fetch_data.py v1.1+ to populate trend data.")

    # Watch for
    if best:
        lines += ["",
                  f"**⚡ Watch for:** {best} is the strongest performer over 30 days. "
                  f"{'If it begins to flatten while others continue rising, watch for rotation out of that market.' if perf.get(best,0)>3 else 'Convergence among all indices at similar levels suggests a macro catalyst — not sector-specific — is driving the move.'}"]
    lines.append("")
    return "\n".join(lines)


def chart_analysis_rates(data, fname):
    fred = data.get("fred_data", {})
    dff   = fred.get("DFF",   {}).get("value")
    dgs10 = fred.get("DGS10", {}).get("value")
    dgs2  = fred.get("DGS2",  {}).get("value")

    lines = [f"### 📊 Chart 2: US Rates & Treasury Yields — 30-Day Trend\n",
             f"![Chart 2]({fname})\n",
             "**What this chart shows:**  ",
             "Three lines map the US interest rate curve: the **Fed Funds Rate** (overnight "
             "policy rate the Fed controls), the **10Y Treasury yield** (long-term borrowing "
             "cost and growth expectations proxy), and the **2Y yield** (near-term Fed rate "
             "path expectations). The gap between 10Y and 2Y is the **yield curve** — "
             "positive = normal (growth expected); negative = **inverted** (historically a "
             "recession warning 12–18 months ahead).\n",
             "**Key Observations:**"]

    if dff and dgs10 and dgs2:
        spread = (dgs10 - dgs2) * 100
        if   spread >  80: curve_state = "steep positive — market pricing in sustained growth"
        elif spread >  30: curve_state = "modestly positive — mild growth optimism"
        elif spread >   0: curve_state = "near-flat — transition zone, watch direction"
        elif spread > -30: curve_state = "mildly inverted — caution signal, historically precedes slowdown"
        else:              curve_state = "deeply inverted — strong historical recession signal"

        gap_ff_10y = dgs10 - dff
        lines += [
            f"- **Fed Funds Rate:** {dff:.2f}% — "
            f"{'well above recent norms; restrictive policy stance' if dff > 4 else 'moderately restrictive' if dff > 2.5 else 'accommodative / easing cycle'}",
            f"- **10Y–2Y Spread:** {spread:+.0f} bps — {curve_state}",
            f"- **10Y vs Fed Funds gap:** {gap_ff_10y:+.2f}% ({gap_ff_10y*100:+.0f} bps) — "
            f"{'market pricing in rate cuts ahead; 10Y below Fed Funds' if gap_ff_10y < 0 else 'market expects rates to stay elevated; 10Y above Fed Funds'}",
        ]

        # 30-day direction from history
        hist10 = fred.get("DGS10",{}).get("history",[])
        if len(hist10) >= 5:
            start_10y = hist10[max(0,len(hist10)-30)]["value"]
            move_10y  = (dgs10 - start_10y) * 100
            lines.append(f"- **30-day 10Y direction:** {move_10y:+.0f} bps — "
                        f"{'yields rising = bond selloff / inflation concern or growth optimism' if move_10y > 5 else 'yields falling = flight to safety or rate cut pricing' if move_10y < -5 else 'yields stable — no major repricing'}")
    else:
        lines.append("- Rate data unavailable. Set FRED_API_KEY in .env and re-run fetch_data.py.")

    watch = ""
    if dff and dgs10 and dgs2:
        spread = (dgs10 - dgs2) * 100
        if spread < 0:
            watch = (f"The yield curve is inverted ({spread:+.0f} bps). Historically this "
                    f"has preceded recessions by 12–18 months. Watch for re-steepening "
                    f"(curve un-inverting) — this often signals a cut cycle beginning and "
                    f"can trigger rapid equity repricing.")
        else:
            watch = (f"The 10Y yield at {dgs10:.2f}% is the key equity valuation lever. "
                    f"A break higher than this session's level would compress equity P/E multiples "
                    f"(higher discount rate = lower present value of future earnings).")

    lines += ["", f"**⚡ Watch for:** {watch if watch else 'No rate data available for signal generation.'}"]
    lines.append("")
    return "\n".join(lines)


def chart_analysis_heatmap(data, fname):
    mkt = data.get("market_data", {})

    # Build change table from JSON
    GROUPS = {
        "Equities":   ["^GSPC","^NDX","^N225","^HSI"],
        "FX":         ["DX-Y.NYB","USDJPY=X","EURUSD=X"],
        "Commodities":["GC=F","CL=F"],
        "Crypto":     ["BTC-USD","ETH-USD"],
    }
    all_ytd, ytd_named = [], []
    green_1d, red_1d, na_1d = 0, 0, 0

    for group, tickers in GROUPS.items():
        for tk in tickers:
            e = mkt.get(tk, {})
            if not e: continue
            p1d  = parse_pct(e.get("change_1d"))
            pytd = parse_pct(e.get("change_ytd"))
            if p1d  is not None: (green_1d if p1d > 0 else red_1d).__iadd__ if False else (green_1d := green_1d+1 if p1d > 0 else green_1d, red_1d := red_1d+1 if p1d <= 0 else red_1d)
            else: na_1d += 1
            if pytd is not None: ytd_named.append((e.get("label",tk), pytd))

    # Fix: recount cleanly
    green_1d = red_1d = na_1d = 0
    for group, tickers in GROUPS.items():
        for tk in tickers:
            e = mkt.get(tk, {})
            p1d = parse_pct(e.get("change_1d"))
            if p1d is None: na_1d += 1
            elif p1d > 0:   green_1d += 1
            else:            red_1d   += 1

    ytd_named.sort(key=lambda x: -x[1])
    best_ytd  = ytd_named[0]  if ytd_named else None
    worst_ytd = ytd_named[-1] if ytd_named else None

    # Cross signals
    gold_1d   = parse_pct(mkt.get("GC=F",    {}).get("change_1d"))
    dxy_1d    = parse_pct(mkt.get("DX-Y.NYB",{}).get("change_1d"))
    spx_1d    = parse_pct(mkt.get("^GSPC",   {}).get("change_1d"))
    btc_1d    = parse_pct(mkt.get("BTC-USD", {}).get("change_1d"))
    jpyusd    = mkt.get("USDJPY=X",{}).get("value")
    jpy_1d    = parse_pct(mkt.get("USDJPY=X",{}).get("change_1d"))

    lines = [f"### 📊 Chart 3: Cross-Asset Performance Heatmap\n",
             f"![Chart 3]({fname})\n",
             "**What this chart shows:**  ",
             "Each **row** is an asset; each **column** is a timeframe (1D = today, "
             "1W = rolling 7 days, YTD = year-to-date). "
             "**Green = gain, Red = loss** — darker color means larger magnitude. "
             "Reading *across a row*: is today's move consistent with the longer trend? "
             "Reading *down a column*: which assets lead or lag on that timeframe? "
             "Assets are grouped: Equities → FX → Commodities → Crypto.\n",
             "**Key Observations:**"]

    # 1D tone
    total_1d = green_1d + red_1d
    if total_1d > 0:
        tone = "broadly Risk-On" if green_1d > red_1d * 1.5 else "broadly Risk-Off" if red_1d > green_1d * 1.5 else "mixed / transitional"
        lines.append(f"- **1D session tone:** {green_1d} assets green vs {red_1d} red ({tone}){' — 1D changes unavailable (prior session data)' if na_1d > 8 else ''}")
    else:
        lines.append("- 1D changes unavailable — live fetch failed this run (prior session data used).")

    # YTD — pre-compute momentum tag to avoid f-string complexity
    if best_ytd:
        best_label  = best_ytd[0]
        best_ticker = next((k for k,v in mkt.items() if isinstance(v,dict) and v.get("label")==best_label), None)
        best_1d     = parse_pct(mkt.get(best_ticker,{}).get("change_1d")) if best_ticker else None
        momentum_tag = "momentum intact (also green 1D)" if best_1d and best_1d > 0 else "watch for momentum fade"
        lines.append(f"- **Best YTD:** {best_ytd[0]} at {best_ytd[1]:+.1f}% — {momentum_tag}")
    if worst_ytd:
        lines.append(f"- **Worst YTD:** {worst_ytd[0]} at {worst_ytd[1]:+.1f}% — "
                    "underperforming on the year; check if 1D is also red (trend continuing) or green (potential stabilization)")

    # Cross-asset signals
    if spx_1d is not None and gold_1d is not None:
        if spx_1d > 0 and gold_1d > 0:
            lines.append(f"- **Risk signal:** Both S&P 500 ({spx_1d:+.2f}%) and Gold ({gold_1d:+.2f}%) are green — this combination signals macro uncertainty rather than clean risk-on (investors buying both growth AND safety)")
        elif spx_1d > 0 and gold_1d < 0:
            lines.append(f"- **Risk signal:** S&P 500 up ({spx_1d:+.2f}%) while Gold down ({gold_1d:+.2f}%) — classic clean risk-on; capital rotating from safe haven to equities")
        elif spx_1d < 0 and gold_1d > 0:
            lines.append(f"- **Risk signal:** S&P 500 down ({spx_1d:+.2f}%) while Gold up ({gold_1d:+.2f}%) — clear risk-off; safe haven demand active")

    if jpyusd is not None:
        if jpyusd > 150:
            lines.append(f"- **USD/JPY at {jpyusd:.2f}:** Yen is weak vs dollar — JPY carry trades active. A sudden JPY strengthening (USD/JPY falling rapidly) can cause forced carry-unwind and sharp cross-asset selloff")
        elif jpyusd < 130:
            lines.append(f"- **USD/JPY at {jpyusd:.2f}:** Yen is strong vs dollar — carry trade unwinding or BOJ policy shift in play")

    if btc_1d is not None and spx_1d is not None:
        corr = abs(btc_1d - spx_1d) < 0.8
        lines.append(f"- **Crypto correlation:** BTC ({btc_1d:+.2f}%) vs S&P 500 ({spx_1d:+.2f}%) — {'moving in-step with equities (correlated risk-on/off)' if corr else 'diverging from equities — crypto-specific catalysts may be at play'}")

    # Watch for
    watch_signals = []
    if spx_1d is None:
        watch_signals.append("Live 1D data unavailable — full cross-asset signal generation requires live fetch. Re-run during market hours.")
    elif gold_1d is not None and spx_1d > 0 and gold_1d > 0:
        watch_signals.append("Both equities and gold advancing — this macro-uncertainty pattern tends to resolve: watch which breaks first.")
    if worst_ytd and worst_ytd[1] < -10:
        watch_signals.append(f"{worst_ytd[0]} is down {worst_ytd[1]:.1f}% YTD — if it starts showing consistent 1D green sessions, that is a potential mean-reversion trade setup.")

    watch = " ".join(watch_signals) if watch_signals else "Monitor the YTD column for any asset where the trend contradicts the 1D move — this divergence often precedes a larger directional move."
    lines += ["", f"**⚡ Watch for:** {watch}"]
    lines.append("")
    return "\n".join(lines)

# ─────────────────────────────────────────────────────────────────
# MARKDOWN BUILDER
# ─────────────────────────────────────────────────────────────────
def build_markdown(data, today, chart_paths):
    meta    = data.get("_meta", {})
    quality = data.get("_quality", {})
    conf    = quality.get("confidence","?")

    lines = []
    lines += [f"# Daily Economic Intelligence Report",
              f"**Date:** {today} | **Generated:** {meta.get('generated_jst','')} | **Confidence:** {conf}\n",
              "---\n",
              "## 1. Executive Summary\n"]
    for b in auto_summary(data):
        lines.append(f"- {b}")
    lines.append("")

    lines += ["---\n\n## 2. Key Indicators Dashboard\n",
              "| Indicator | Value | 1D | YTD | As Of | Source |",
              "|---|---|---|---|---|---|"]

    DASH = [("fred_data","DFF"), ("fred_data","DGS10"), ("fred_data","DGS2"),
            ("fred_data","ECBDFR"), ("fred_data","IRSTCI01JPM156N"),
            ("market_data","^GSPC"), ("market_data","^NDX"),
            ("market_data","^N225"), ("market_data","^HSI"),
            ("market_data","DX-Y.NYB"), ("market_data","USDJPY=X"), ("market_data","EURUSD=X"),
            ("market_data","GC=F"), ("market_data","CL=F"),
            ("market_data","BTC-USD"), ("market_data","ETH-USD")]

    for section, key in DASH:
        e = data.get(section,{}).get(key,{})
        if not e or "value" not in e or e.get("value") is None: continue
        v   = e["value"]
        src = "FRED" if section == "fred_data" else "Yahoo Finance"
        sf  = stale_flag(e)
        lines.append(f"| {e.get('label',key)}{sf} | {fmt_val(v)} | "
                     f"{e.get('change_1d','N/A')} | {e.get('change_ytd','N/A')} | "
                     f"{e.get('as_of','N/A')} | {src} |")
    lines.append("\n*⚠ = value older than freshness threshold. All changes are from prior reading.*\n")

    lines += ["---\n\n## 3. Market Narrative\n",
              f"### Risk Regime: {auto_regime(data)}\n",
              auto_narrative(data), ""]

    lines += ["---\n\n## 4. Chart Analysis\n",
              chart_analysis_equity(data, chart_paths[0].name),
              chart_analysis_rates(data, chart_paths[1].name),
              chart_analysis_heatmap(data, chart_paths[2].name)]

    lines += ["---\n\n## 5. Forward Calendar (Next 24–72 Hours)\n"]
    cal = [c for c in data.get("calendar",[]) if isinstance(c,dict) and "release_name" in c]
    if cal:
        lines += ["| Date | Event | Importance |","|---|---|---|"]
        for c in cal:
            lines.append(f"| {c.get('date','')} | {c.get('release_name','')} | {c.get('importance','')} |")
    else:
        lines.append("*No upcoming events in calendar data (FRED API key required for this section).*")
    lines.append("")

    lines += ["---\n\n## 6. Data Confidence & Limitations\n",
              f"**Overall Confidence: {conf}**\n"]
    flags = quality.get("flags",[])
    if flags:
        for f in flags: lines.append(f"- {f}")
    else:
        lines.append("All data sources clean — no flags raised.")
    lines += ["", "---",
              "*This report is for informational and educational purposes only. Not financial advice.*"]

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# PDF BUILDER
# ─────────────────────────────────────────────────────────────────
def build_pdf(data, today, md_text, chart_paths):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Image, Table, TableStyle, HRFlowable)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    H1C = colors.HexColor("#0d1117")
    H2C = colors.HexColor("#1f6feb")
    BGC = colors.HexColor("#f6f8fa")

    pdf_path = REPORTS_DIR / f"econ-insight_{today}.pdf"
    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4,
                            topMargin=1.8*cm, bottomMargin=1.8*cm,
                            leftMargin=2*cm, rightMargin=2*cm)
    styles = getSampleStyleSheet()

    sH1 = ParagraphStyle("H1", parent=styles["Title"],
                          fontSize=20, textColor=H1C, spaceAfter=4)
    sH2 = ParagraphStyle("H2", parent=styles["Heading2"],
                          fontSize=12, textColor=H2C, spaceBefore=16, spaceAfter=4)
    sBOD = ParagraphStyle("BOD", parent=styles["Normal"],
                           fontSize=8.5, leading=13, spaceAfter=4)
    sSUB = ParagraphStyle("SUB", parent=styles["Normal"],
                           fontSize=7, leading=10, textColor=colors.HexColor("#6e7781"))
    sBUL = ParagraphStyle("BUL", parent=styles["Normal"],
                           fontSize=8.5, leading=13, leftIndent=10, spaceAfter=2)

    meta    = data.get("_meta", {})
    quality = data.get("_quality", {})
    conf    = quality.get("confidence","?")
    conf_color = {"High":"#3fb950","Medium":"#ffa657","Low":"#f78166"}.get(conf,"#8b949e")

    elems = []

    # Header
    elems.append(Paragraph("Daily Economic Intelligence Report", sH1))
    elems.append(Paragraph(
        f"<font color='#6e7781'>Date: {today} &nbsp;|&nbsp; "
        f"Generated: {meta.get('generated_jst','')} &nbsp;|&nbsp; "
        f"Confidence: </font><font color='{conf_color}'><b>{conf}</b></font>", sBOD))
    elems.append(HRFlowable(width="100%", thickness=1,
                             color=colors.HexColor("#d0d7de"), spaceAfter=8))

    # Section 1 — Summary
    elems.append(Paragraph("1. Executive Summary", sH2))
    for b in auto_summary(data):
        elems.append(Paragraph(f"• {b}", sBUL))

    # Section 2 — Dashboard
    elems.append(Paragraph("2. Key Indicators Dashboard", sH2))
    DASH = [("fred_data","DFF"), ("fred_data","DGS10"), ("fred_data","DGS2"),
            ("fred_data","ECBDFR"), ("fred_data","IRSTCI01JPM156N"),
            ("market_data","^GSPC"), ("market_data","^NDX"),
            ("market_data","^N225"), ("market_data","^HSI"),
            ("market_data","DX-Y.NYB"), ("market_data","USDJPY=X"), ("market_data","EURUSD=X"),
            ("market_data","GC=F"), ("market_data","CL=F"),
            ("market_data","BTC-USD"), ("market_data","ETH-USD")]

    rows = [["Indicator","Value","1D Change","YTD Change","As Of"]]
    for section, key in DASH:
        e = data.get(section,{}).get(key,{})
        if not e or "value" not in e or e.get("value") is None: continue
        v   = e["value"]
        sf  = " ⚠" if e.get("freshness",{}).get("is_stale") else ""
        rows.append([f"{e.get('label',key)}{sf}",
                     fmt_val(v),
                     str(e.get("change_1d","N/A"))[:14],
                     str(e.get("change_ytd","N/A"))[:14],
                     e.get("as_of","N/A")])

    if len(rows) > 1:
        t = Table(rows, repeatRows=1, hAlign="LEFT",
                  colWidths=[5.2*cm,2.4*cm,2.8*cm,2.8*cm,2.5*cm])
        ts = TableStyle([
            ("BACKGROUND",   (0,0),(-1,0), colors.HexColor("#0d1117")),
            ("TEXTCOLOR",    (0,0),(-1,0), colors.white),
            ("FONTSIZE",     (0,0),(-1,-1), 7.5),
            ("FONTNAME",     (0,0),(-1,0), "Helvetica-Bold"),
            ("GRID",         (0,0),(-1,-1), 0.3, colors.HexColor("#d0d7de")),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.white, colors.HexColor("#f6f8fa")]),
            ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
            ("TOPPADDING",   (0,0),(-1,-1), 3),
            ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ])
        t.setStyle(ts)
        elems.append(t)
    else:
        elems.append(Paragraph("No indicator data available — check Section 6.", sBOD))

    elems.append(Paragraph("⚠ = data older than freshness threshold", sSUB))

    # Section 3 — Narrative
    elems.append(Paragraph("3. Market Narrative", sH2))
    elems.append(Paragraph(f"<b>Risk Regime: {auto_regime(data)}</b>", sBOD))
    for para in auto_narrative(data).split("\n\n"):
        clean = para.replace("**","<b>",1).replace("**","</b>",1)
        clean = clean.replace("*","<i>",1).replace("*","</i>",1) if "*" in clean else clean
        elems.append(Paragraph(clean, sBOD))

    # Section 4 — Charts
    elems.append(Paragraph("4. Chart Pack", sH2))
    chart_titles = ["Chart 1: Major Equity Indices — 30-day normalized",
                    "Chart 2: US Rates & Yields — 30-day trend",
                    "Chart 3: Cross-Asset Performance Heatmap"]
    for cp, title in zip(chart_paths, chart_titles):
        elems.append(Paragraph(f"<b>{title}</b>", sSUB))
        if cp.exists():
            elems.append(Image(str(cp), width=16.5*cm, height=7.4*cm))
        else:
            elems.append(Paragraph("[Chart file missing]", sSUB))
        elems.append(Spacer(1, 0.25*cm))

    # Section 5 — Calendar
    elems.append(Paragraph("5. Forward Calendar (Next 24–72 Hours)", sH2))
    cal = [c for c in data.get("calendar",[]) if isinstance(c,dict) and "release_name" in c]
    if cal:
        cal_rows = [["Date","Event","Importance"]]
        for c in cal:
            cal_rows.append([c.get("date",""), c.get("release_name",""), c.get("importance","")])
        ct = Table(cal_rows, repeatRows=1, hAlign="LEFT",
                   colWidths=[2.8*cm,10.5*cm,2.5*cm])
        ct.setStyle(TableStyle([
            ("BACKGROUND",  (0,0),(-1,0), colors.HexColor("#0d1117")),
            ("TEXTCOLOR",   (0,0),(-1,0), colors.white),
            ("FONTSIZE",    (0,0),(-1,-1), 7.5),
            ("GRID",        (0,0),(-1,-1), 0.3, colors.HexColor("#d0d7de")),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, colors.HexColor("#f6f8fa")]),
        ]))
        elems.append(ct)
    else:
        elems.append(Paragraph("No upcoming events. (FRED_API_KEY required for calendar.)", sBOD))

    # Section 6 — Confidence
    elems.append(Paragraph("6. Data Confidence & Limitations", sH2))
    elems.append(Paragraph(
        f"Overall Confidence: <font color='{conf_color}'><b>{conf}</b></font>", sBOD))
    for f in quality.get("flags",[]):
        color = "#f78166" if "[ERROR]" in f else "#ffa657" if "[STALE]" in f else "#8b949e"
        elems.append(Paragraph(f"<font color='{color}'>• {f}</font>", sSUB))
    if not quality.get("flags"):
        elems.append(Paragraph("• All sources clean — no flags raised.", sSUB))

    elems.append(Spacer(1, 0.6*cm))
    elems.append(HRFlowable(width="100%", thickness=0.5,
                             color=colors.HexColor("#d0d7de"), spaceAfter=4))
    elems.append(Paragraph(
        "This report is for informational and educational purposes only. Not financial advice.", sSUB))

    doc.build(elems)
    return pdf_path


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) > 1:
        json_path = Path(sys.argv[1])
    else:
        today_str = datetime.now().strftime("%Y-%m-%d")
        json_path = DATA_DIR / f"raw_{today_str}.json"

    if not json_path.exists():
        print(f"✗ JSON not found: {json_path}")
        print(f"  Run: python fetch_data.py")
        sys.exit(1)

    data  = json.load(open(json_path, encoding="utf-8"))
    today = data.get("_meta",{}).get("date", datetime.now().strftime("%Y-%m-%d"))
    gen   = data.get("_meta",{}).get("generated_jst","")
    conf  = data.get("_quality",{}).get("confidence","?")
    footer = f"Source: fetch_data.py v{data.get('_meta',{}).get('script_version','?')} | {gen}"

    print(f"\nRendering report for {today}  (Confidence: {conf})")
    print("─" * 50)

    c1, ok1 = chart_equity(data, today, footer)
    print(f"  Chart 1 (equity)   {'✓ with trend data' if ok1 else '⚠ no history — re-run fetch_data.py v1.1+'}")
    c2, ok2 = chart_rates(data, today, footer)
    print(f"  Chart 2 (rates)    {'✓ with trend data' if ok2 else '⚠ no history or FRED key missing'}")
    c3, ok3 = chart_heatmap(data, today, footer)
    print(f"  Chart 3 (heatmap)  {'✓ with change data' if ok3 else '⚠ 1D changes N/A (live fetch failed)'}")

    chart_paths = [c1, c2, c3]

    md = build_markdown(data, today, chart_paths)
    md_path = REPORTS_DIR / f"econ-insight_{today}.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"  Markdown           ✓  {md_path.name}")

    pdf_path = build_pdf(data, today, md, chart_paths)
    print(f"  PDF                ✓  {pdf_path.name}")

    print(f"\n✓ Done → open:  reports/{pdf_path.name}")

if __name__ == "__main__":
    main()
