#!/usr/bin/env python3
"""
fetch_data.py
═══════════════════════════════════════════════════════════════════
Economic Intelligence System — Layer 1: Data Acquisition
═══════════════════════════════════════════════════════════════════
Fetches all macro & market indicators defined in Output Contract v2.
Outputs: /data/raw_YYYY-MM-DD.json  (used by Cowork synthesis layer)
         /data/state_latest.json    (used for delta comparisons next run)
         /logs/errors_YYYY-MM-DD.txt

Sources:
  - FRED API       → US macro (CPI, rates, yields, GDP) + intl CB rates
  - yfinance       → Market prices (indices, FX, commodities, crypto)
  - World Bank API → Cross-country GDP growth & inflation (annual)
  - FRED Calendar  → Upcoming economic releases (next 7 days)

Setup:
  1. pip install -r requirements.txt
  2. Copy .env.example → .env and fill in your FRED API key
     (Free key: https://fred.stlouisfed.org/docs/api/api_key.html)
  3. python fetch_data.py
═══════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import time
import logging
import traceback
from datetime import datetime, timedelta, date
from pathlib import Path

import pytz
import requests
from dotenv import load_dotenv

# Windows consoles default to a legacy code page (e.g. cp932 on JP locale)
# that cannot encode the arrow/check glyphs used in log messages — force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ─────────────────────────────────────────────────────────────────
# 0. BOOTSTRAP — Environment, Paths, Logging
# ─────────────────────────────────────────────────────────────────

load_dotenv()

# All timestamps in JST (Kumamoto)
JST     = pytz.timezone("Asia/Tokyo")
NOW_JST = datetime.now(JST)
TODAY   = NOW_JST.strftime("%Y-%m-%d")

# Project directories (relative to this script)
BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / "data"
LOGS_DIR    = BASE_DIR / "logs"
REPORTS_DIR = BASE_DIR / "reports"
CHARTS_DIR  = BASE_DIR / "charts"

for d in [DATA_DIR, LOGS_DIR, REPORTS_DIR, CHARTS_DIR]:
    d.mkdir(exist_ok=True)

# Logging: write to file + console simultaneously
log_file = LOGS_DIR / f"errors_{TODAY}.txt"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# 1. CONFIGURATION — All indicators defined in Output Contract v2
# ─────────────────────────────────────────────────────────────────

FRED_API_KEY      = os.getenv("FRED_API_KEY")
# Optional: CoinGecko free "Demo" key unlocks higher rate limits for the /global
# endpoint. The endpoint also works keyless (stricter limits), so Tier-2 dominance
# degrades gracefully when this is unset.
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

# FRED series → (human label, category, unit)
# unit: "percent" → changes reported in bps; "index"/"billions_usd" → reported as %
FRED_SERIES = {
    # ── US Macro ──────────────────────────────────────
    "DFF":              ("Fed Funds Rate",           "us_macro",   "percent"),
    "DGS10":            ("10Y Treasury Yield",        "us_macro",   "percent"),
    "DGS2":             ("2Y Treasury Yield",         "us_macro",   "percent"),
    "CPIAUCSL":         ("CPI (All Urban, All Items)","us_macro",   "index"),
    "CPILFESL":         ("Core CPI (ex Food/Energy)", "us_macro",   "index"),
    "GDP":              ("Real GDP (quarterly)",      "us_macro",   "billions_usd"),
    "DFII10":           ("10Y Real Yield (TIPS)",     "us_macro",   "percent"),
    # ── International CB Rates (available via FRED) ───
    "ECBDFR":           ("ECB Deposit Rate",          "intl_macro", "percent"),
    # UK: SONIA daily rate tracks the BOE Bank Rate within a few bps.
    # (The old BOERUKMINLAMFSAM series was discontinued by FRED.)
    "IUDSOIA":          ("BOE Bank Rate (SONIA)",     "intl_macro", "percent"),
    # Japan short-term rate (BOJ policy rate proxy)
    "IRSTCI01JPM156N":  ("BOJ Short-Term Rate",       "intl_macro", "percent"),
    # NOTE: Japan CPI dropped — the OECD MEI FRED series were discontinued
    # in 2024. Japan inflation is already covered via World Bank below.
}

# yfinance tickers → (human label, category)
YFINANCE_TICKERS = {
    # ── Equity Indices ─────────────────────────────────
    "^GSPC":     ("S&P 500",       "equity"),
    "^NDX":      ("NASDAQ 100",    "equity"),
    "^DJI":      ("Dow Jones",     "equity"),
    "^N225":     ("Nikkei 225",    "equity"),
    "^HSI":      ("Hang Seng",     "equity"),
    # ── FX ────────────────────────────────────────────
    "DX-Y.NYB":  ("DXY Index",     "fx"),
    "USDJPY=X":  ("USD/JPY",       "fx"),
    "EURUSD=X":  ("EUR/USD",       "fx"),
    "GBPUSD=X":  ("GBP/USD",       "fx"),
    # ── Commodities ───────────────────────────────────
    "GC=F":      ("Gold",          "commodity"),
    "CL=F":      ("WTI Crude",     "commodity"),
    "BZ=F":      ("Brent Crude",   "commodity"),
    # ── Crypto ────────────────────────────────────────
    "BTC-USD":   ("Bitcoin",       "crypto"),
    "ETH-USD":   ("Ethereum",      "crypto"),
}

# World Bank indicators → (label, list of ISO2 country codes)
WORLD_BANK_INDICATORS = {
    "FP.CPI.TOTL.ZG":    ("Inflation, annual %",   ["US", "JP", "GB", "DE", "CN"]),
    "NY.GDP.MKTP.KD.ZG": ("GDP growth, annual %",  ["US", "JP", "GB", "DE", "CN"]),
}

# Staleness is judged per-series in check_freshness() from each series' own
# observation cadence (see compute_baselines → "cadence_days"), measured in days.
# This tuple is kept only as documentation of the asset categories in use.
# crypto_* categories are intraday (funding settles 3x/day), so check_freshness
# measures their staleness in HOURS via an explicit timestamp.
FRESHNESS_CATEGORIES = (
    "equity", "fx", "crypto", "commodity", "us_macro", "intl_macro",
    "crypto_derivatives", "crypto_liquidity", "crypto_vol",
)

# Upcoming economic releases to track (FRED release IDs).
# IDs verified against the FRED /release endpoint — the previous set was wrong
# (82=Economic Report of the President, 175=Personal Income by County, 18=H.15
# Rates, 50 is actually Employment Situation). ISM PMI / Consumer Confidence are
# NOT published on FRED (private surveys), so they are intentionally omitted.
FRED_RELEASE_IDS = {
    50: "Employment Situation (NFP + Unemployment)",
    10: "Consumer Price Index (CPI)",
    54: "Personal Income & Outlays (PCE inflation)",
    46: "Producer Price Index (PPI)",
    53: "Gross Domestic Product (GDP)",
    9:  "Advance Retail Sales",
    11: "Employment Cost Index",
}

# How many days ahead the forward calendar looks.
CALENDAR_HORIZON_DAYS = 14

# ─────────────────────────────────────────────────────────────────
# 2. UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def ts_now() -> str:
    """Current timestamp in JST as string."""
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")


def pct_change(current, prior) -> str:
    """Return formatted % change, or N/A if inputs invalid."""
    try:
        if prior is not None and prior != 0:
            return f"{((current - prior) / abs(prior)) * 100:+.2f}%"
    except Exception:
        pass
    return "N/A"


def bps_change(current, prior) -> str:
    """Return basis-point change (for rate/yield series)."""
    try:
        if prior is not None:
            return f"{(current - prior) * 100:+.1f} bps"
    except Exception:
        pass
    return "N/A"


def check_freshness(data_date_str: str, category: str, cadence_days: float = 1, as_of_ts: float = None) -> dict:
    """
    Decide whether a series' latest reading is stale, relative to how often the
    series actually updates (`cadence_days`, inferred from its history).

    Two granularities:
      • Sub-day cadence (e.g. crypto funding settles every 8h → cadence ~0.33) is
        judged in HOURS against an explicit `as_of_ts` (epoch seconds), tolerating
        ~3 settlement cycles. This is why a 0.33 cadence is not truncated to 0.
      • Daily/monthly/quarterly bars are judged in DAYS: every such bar is dated at
        the session/period (midnight), so an hours-based limit would flag even
        today's data as "4h+ old". Daily (cadence 1) tolerates ~6 days (Friday close
        stays fresh the following week); monthly (~30) ~3 months; quarterly likewise.
    """
    try:
        now = datetime.now(JST)

        # ── Intraday series: measure in hours against the real timestamp ──
        if cadence_days < 1 and as_of_ts is not None:
            data_dt   = datetime.fromtimestamp(as_of_ts, JST)
            age_hours = (now - data_dt).total_seconds() / 3600
            allowed_h = max(12, cadence_days * 24 * 3)  # ~3 update cycles, floor 12h
            if age_hours > allowed_h:
                return {
                    "is_stale":    True,
                    "reason":      f"Data is {age_hours:.1f}h old (cadence ~{cadence_days*24:.0f}h, limit {allowed_h:.0f}h)",
                    "last_updated": data_date_str,
                }
            return {"is_stale": False, "last_updated": data_date_str}

        clean = data_date_str[:10]  # take YYYY-MM-DD portion
        data_date = datetime.strptime(clean, "%Y-%m-%d").date()
        age_days  = (now.date() - data_date).days

        # Allowed age = a few publication cycles + weekend/holiday grace.
        allowed = max(6, int(cadence_days) * 3 + 3)

        if age_days > allowed:
            return {
                "is_stale":    True,
                "reason":      f"Data is {age_days}d old (cadence ~{cadence_days}d, limit {allowed}d)",
                "last_updated": data_date_str,
            }
        return {"is_stale": False, "last_updated": data_date_str}

    except Exception as e:
        return {
            "is_stale": True,
            "reason":   f"Freshness check failed: {e}",
            "last_updated": data_date_str,
        }


def compute_baselines(series, unit: str) -> dict:
    """
    Given a pandas Series of historical values (index=DatetimeIndex),
    compute all baseline comparisons required by Output Contract v2:
      - vs prior reading (1D delta for daily series)
      - vs YTD start
      - vs 1 year ago
      - vs 1 week ago (5 trading sessions, for market data)
    Returns dict of baseline values + formatted change strings.
    """
    result = {}

    latest_val  = float(series.iloc[-1])
    latest_date = str(series.index[-1].date())
    result["value"]   = latest_val
    result["as_of"]   = latest_date

    # Recent daily history (last 60 readings) — consumed by the 30-day
    # trend charts in render_report.py via get_history().
    tail = series.tail(60)
    result["history"] = [
        {"date": str(idx.date()), "value": round(float(val), 6)}
        for idx, val in tail.items()
    ]

    # Infer how often this series updates (median day-gap of recent readings),
    # so check_freshness can judge staleness against the right cadence:
    # ~1 for daily bars, ~30 for monthly, ~90 for quarterly.
    idx = series.index
    if len(idx) >= 3:
        recent = idx[-12:]
        gaps = [(recent[i] - recent[i - 1]).days for i in range(1, len(recent))]
        gaps = sorted(g for g in gaps if g > 0)
        result["cadence_days"] = gaps[len(gaps) // 2] if gaps else 1
    else:
        result["cadence_days"] = 1

    # Change function: bps for rates/yields, % for everything else
    delta_fn = bps_change if unit == "percent" else pct_change

    # ── Prior reading ──────────────────────────────────────────────
    if len(series) >= 2:
        result["prior_value"] = float(series.iloc[-2])
        result["prior_date"]  = str(series.index[-2].date())
        result["change_1d"]   = delta_fn(latest_val, result["prior_value"])
    else:
        result["prior_value"] = None
        result["prior_date"]  = None
        result["change_1d"]   = "N/A"

    # ── 1-week ago (5 trading sessions) ───────────────────────────
    if len(series) >= 6:
        result["week_ago_value"] = float(series.iloc[-6])
        result["change_1w"]      = delta_fn(latest_val, result["week_ago_value"])
    else:
        result["week_ago_value"] = None
        result["change_1w"]      = "N/A"

    # ── YTD start ──────────────────────────────────────────────────
    ytd = series[series.index.year == NOW_JST.year]
    if not ytd.empty:
        result["ytd_start"]      = float(ytd.iloc[0])
        result["ytd_start_date"] = str(ytd.index[0].date())
        result["change_ytd"]     = delta_fn(latest_val, result["ytd_start"])
    else:
        result["ytd_start"]      = None
        result["ytd_start_date"] = None
        result["change_ytd"]     = "N/A"

    # ── 1 year ago ─────────────────────────────────────────────────
    one_yr_ago = NOW_JST.date() - timedelta(days=365)
    yr_series  = series[series.index.date <= one_yr_ago]
    if not yr_series.empty:
        result["1y_ago_value"] = float(yr_series.iloc[-1])
        result["1y_ago_date"]  = str(yr_series.index[-1].date())
        result["change_1y"]    = delta_fn(latest_val, result["1y_ago_value"])
    else:
        result["1y_ago_value"] = None
        result["1y_ago_date"]  = None
        result["change_1y"]    = "N/A"

    return result

# ─────────────────────────────────────────────────────────────────
# 3. FRED FETCHER
# ─────────────────────────────────────────────────────────────────

def fetch_fred() -> dict:
    """
    Fetch all FRED series defined in FRED_SERIES.
    Each series gets: value, baselines (1D/1W/YTD/1Y), freshness, source.
    Returns dict keyed by series_id.
    """
    results = {}

    if not FRED_API_KEY:
        log.warning("FRED_API_KEY not set in .env — skipping FRED fetch")
        return {"_error": "FRED_API_KEY missing. Get free key at https://fred.stlouisfed.org/docs/api/api_key.html"}

    try:
        from fredapi import Fred
        fred = Fred(api_key=FRED_API_KEY)
    except ImportError:
        log.error("fredapi not installed. Run: pip install fredapi")
        return {"_error": "fredapi not installed"}

    fetch_start = (date.today() - timedelta(days=400)).isoformat()

    for series_id, (label, category, unit) in FRED_SERIES.items():
        try:
            series = fred.get_series(series_id, observation_start=fetch_start).dropna()

            if series.empty:
                log.warning(f"FRED {series_id}: no data returned")
                results[series_id] = {"label": label, "category": category, "error": "Empty series"}
                continue

            baselines = compute_baselines(series, unit)
            freshness = check_freshness(baselines["as_of"], category, baselines.get("cadence_days", 1))

            results[series_id] = {
                "label":      label,
                "category":   category,
                "unit":       unit,
                "source":     "FRED (Federal Reserve Bank of St. Louis)",
                "source_url": f"https://fred.stlouisfed.org/series/{series_id}",
                "freshness":  freshness,
                **baselines,
            }
            log.info(f"  FRED {series_id:25s} → {baselines['value']:.4f}  [{baselines['as_of']}]  1D: {baselines['change_1d']}")

        except Exception as e:
            log.error(f"FRED {series_id} failed: {e}\n{traceback.format_exc()}")
            results[series_id] = {"label": label, "category": category, "error": str(e)}

    return results

# ─────────────────────────────────────────────────────────────────
# 4. YFINANCE FETCHER
# ─────────────────────────────────────────────────────────────────

def fetch_yfinance() -> dict:
    """
    Fetch market prices for all tickers in YFINANCE_TICKERS.
    Downloads 400 days of daily closes in one batch call (efficient).
    Each ticker gets: value, baselines (1D/1W/YTD/1Y), freshness, source.
    """
    results = {}

    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance not installed. Run: pip install yfinance")
        return {"_error": "yfinance not installed"}

    tickers_str = " ".join(YFINANCE_TICKERS.keys())

    # NOTE: do NOT pass a custom `session=`. yfinance >= 1.x bundles curl_cffi
    # and impersonates a browser internally to avoid Yahoo's 429 rate-limiting.
    # Passing our own curl_cffi session previously broke on a curl_cffi cookie-API
    # change ("'str' object has no attribute 'name'"); letting yfinance manage its
    # own session keeps it in sync with the curl_cffi version yfinance pins.
    # threads=False avoids connection-pool exhaustion across the 14-ticker batch.
    raw = None
    log.info("  Downloading batch price data from Yahoo Finance...")
    for attempt in range(1, 4):
        try:
            raw = yf.download(
                tickers=tickers_str,
                period="400d",
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            if raw is not None and not raw.empty:
                break
            log.warning(f"  Batch download empty (attempt {attempt}/3)")
        except Exception as e:
            log.warning(f"  Batch download failed (attempt {attempt}/3): {e}")
        if attempt < 3:
            time.sleep(3 * attempt)

    for ticker, (label, category) in YFINANCE_TICKERS.items():
        try:
            # Extract close series for this ticker
            close_series = None

            if raw is not None and not raw.empty:
                try:
                    # Multi-ticker: columns are (field, ticker) after download
                    if ("Close", ticker) in raw.columns:
                        close_series = raw[("Close", ticker)].dropna()
                    elif hasattr(raw.columns, "levels"):
                        # MultiIndex: try both orderings
                        try:
                            close_series = raw["Close"][ticker].dropna()
                        except Exception:
                            close_series = raw[ticker]["Close"].dropna()
                except Exception:
                    pass

            # Per-ticker fallback if batch extraction failed
            if close_series is None or close_series.empty:
                log.warning(f"  Batch extraction failed for {ticker} — falling back to single download")
                time.sleep(1)
                single = yf.download(
                    ticker, period="400d", interval="1d",
                    auto_adjust=True, progress=False,
                    threads=False,
                )
                close_series = single["Close"].dropna() if not single.empty else None

            if close_series is None or close_series.empty:
                log.warning(f"  {ticker}: no data available from any source")
                results[ticker] = {"label": label, "category": category, "error": "No data returned"}
                continue

            baselines = compute_baselines(close_series, unit="price")
            freshness = check_freshness(baselines["as_of"], category, baselines.get("cadence_days", 1))

            results[ticker] = {
                "label":      label,
                "category":   category,
                "unit":       "price",
                "source":     "Yahoo Finance (yfinance)",
                "source_url": f"https://finance.yahoo.com/quote/{ticker}",
                "freshness":  freshness,
                **baselines,
            }
            log.info(f"  yfinance {ticker:12s} → {baselines['value']:.4f}  [{baselines['as_of']}]  1D: {baselines['change_1d']}")

        except Exception as e:
            log.error(f"yfinance {ticker} failed: {e}\n{traceback.format_exc()}")
            results[ticker] = {"label": label, "category": category, "error": str(e)}

    return results

# ─────────────────────────────────────────────────────────────────
# 5. WORLD BANK FETCHER
# ─────────────────────────────────────────────────────────────────

def fetch_world_bank() -> dict:
    """
    Fetch annual macro data from World Bank public API (no key required).
    Returns GDP growth and inflation for key economies.
    Data is annual — used for structural context, not daily movement.
    """
    results = {}
    BASE    = "https://api.worldbank.org/v2"

    for indicator_id, (label, countries) in WORLD_BANK_INDICATORS.items():
        results[indicator_id] = {"label": label, "countries": {}}

        for country in countries:
            try:
                url  = f"{BASE}/country/{country}/indicator/{indicator_id}?format=json&mrv=3&per_page=3"
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                data = resp.json()

                # data[1] is list of observations (most recent first)
                if not data or len(data) < 2 or not data[1]:
                    results[indicator_id]["countries"][country] = {"error": "No data returned"}
                    continue

                obs = [d for d in data[1] if d.get("value") is not None]
                if not obs:
                    results[indicator_id]["countries"][country] = {"error": "All values null"}
                    continue

                latest = obs[0]
                prior  = obs[1] if len(obs) > 1 else None

                results[indicator_id]["countries"][country] = {
                    "value":       round(latest["value"], 2),
                    "year":        latest["date"],
                    "prior_value": round(prior["value"], 2) if prior else None,
                    "prior_year":  prior["date"] if prior else None,
                    "change_yoy":  pct_change(latest["value"], prior["value"]) if prior else "N/A",
                    "source":      "World Bank",
                    "source_url":  f"https://data.worldbank.org/indicator/{indicator_id}",
                }
                log.info(f"  World Bank {country} {indicator_id[:20]:20s} → {latest['value']:.2f}% ({latest['date']})")

            except Exception as e:
                log.error(f"World Bank {country}/{indicator_id} failed: {e}")
                results[indicator_id]["countries"][country] = {"error": str(e)}

    return results

# ─────────────────────────────────────────────────────────────────
# 6. ECONOMIC CALENDAR (via FRED releases API)
# ─────────────────────────────────────────────────────────────────

def fetch_economic_calendar() -> list:
    """
    Fetch upcoming major economic data releases from FRED (next
    CALENDAR_HORIZON_DAYS days). Requires FRED API key. Returns list sorted by date.
    Note: FRED only covers US macro releases.

    Key detail: FRED's /release/dates returns only PAST dates that already have
    data unless `include_release_dates_with_no_data=true` is set — that flag is
    what surfaces the future scheduled dates. We then filter to our horizon.
    """
    if not FRED_API_KEY:
        log.warning("FRED_API_KEY missing — skipping calendar fetch")
        return [{"error": "FRED_API_KEY required for calendar fetch"}]

    upcoming   = []
    today      = date.today()
    today_str  = today.isoformat()
    horizon    = today + timedelta(days=CALENDAR_HORIZON_DAYS)
    seen       = set()  # (release_id, date) — dedupe repeated schedule rows

    for release_id, release_name in FRED_RELEASE_IDS.items():
        try:
            url = (
                f"https://api.stlouisfed.org/fred/release/dates"
                f"?release_id={release_id}"
                f"&realtime_start={today_str}"
                f"&realtime_end=9999-12-31"
                f"&include_release_dates_with_no_data=true"  # surface FUTURE scheduled dates
                f"&sort_order=asc"
                f"&api_key={FRED_API_KEY}"
                f"&file_type=json"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            for entry in data.get("release_dates", []):
                release_date = entry.get("date", "")
                try:
                    rd = date.fromisoformat(release_date)
                except ValueError:
                    continue
                if today <= rd <= horizon and (release_id, release_date) not in seen:
                    seen.add((release_id, release_date))
                    upcoming.append({
                        "release_name": release_name,
                        "release_id":   release_id,
                        "date":         release_date,
                        "importance":   "High",  # all monitored releases are high-importance
                        "source":       "FRED",
                        "source_url":   f"https://fred.stlouisfed.org/release?release_id={release_id}",
                    })

        except Exception as e:
            log.error(f"Calendar fetch for release_id={release_id} failed: {e}")

    upcoming.sort(key=lambda x: x.get("date", ""))
    log.info(f"  Calendar: {len(upcoming)} major US releases in next {CALENDAR_HORIZON_DAYS} days")
    return upcoming

# ─────────────────────────────────────────────────────────────────
# 6B. CRYPTO-NATIVE QUANT DATA (derivatives, liquidity, volatility)
# ─────────────────────────────────────────────────────────────────

def _http_get_json(url: str, headers: dict = None, attempts: int = 3):
    """
    GET JSON with the same retry/backoff used by fetch_yfinance() — 3 attempts,
    sleep 3 * attempt between them. Raises the last exception if all fail.
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=12)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_exc = e
            if attempt < attempts:
                time.sleep(3 * attempt)
    raise last_exc


def _crypto_entry(label, category, points, unit, source, source_url,
                  cadence_days, as_of_ts, no_ytd=False):
    """
    Build a crypto data point in the same shape as fred_data/market_data entries.
    `points` is a list of (epoch_seconds, value); compute_baselines() does the rest.
    Funding/LS ratios have no meaningful YTD/1Y baseline → no_ytd sets them to N/A.
    """
    import pandas as pd
    idx    = pd.to_datetime([p[0] for p in points], unit="s")
    series = pd.Series([p[1] for p in points], index=idx).dropna()
    if series.empty:
        raise ValueError("empty series")

    baselines = compute_baselines(series, unit=unit)
    baselines["cadence_days"] = cadence_days  # explicit (intraday gaps infer poorly)
    if no_ytd:
        baselines["change_ytd"] = "N/A"
        baselines["change_1y"]  = "N/A"

    freshness = check_freshness(baselines["as_of"], category, cadence_days, as_of_ts)
    return {
        "label":      label,
        "category":   category,
        "unit":       unit,
        "source":     source,
        "source_url": source_url,
        "freshness":  freshness,
        **baselines,
    }


def fetch_crypto_data() -> dict:
    """
    Crypto-native quant signals — same Output Contract shape as fetch_fred() /
    fetch_yfinance(). Each source is fetched independently so one failure can't
    take down the rest; failures land as {label, category, error} (counted by
    validate_output) rather than crashing the pipeline.

      Tier 1 (no key): Binance funding / OI / long-short ratio, Bybit funding x-check
      Tier 2:          CoinGecko dominance, DeFiLlama stablecoin supply, Deribit DVOL
      Tier 3:          Spot ETF net flow → explicit not-implemented stub
    """
    results = {}
    BINANCE = "https://fapi.binance.com"
    SYMS    = [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH")]

    # ── Bybit funding (fetched first, used as cross-check on Binance funding) ──
    bybit_funding = {}
    for sym, tag in SYMS:
        try:
            d = _http_get_json(f"https://api.bybit.com/v5/market/funding/history"
                               f"?category=linear&symbol={sym}&limit=1")
            row = d.get("result", {}).get("list", [{}])[0]
            bybit_funding[tag] = float(row["fundingRate"]) * 100
        except Exception as e:
            log.warning(f"  Bybit funding {tag} cross-check failed: {e}")

    # ── Tier 1a: Binance funding rate (BTC, ETH perp) ──
    for sym, tag in SYMS:
        try:
            d = _http_get_json(f"{BINANCE}/fapi/v1/fundingRate?symbol={sym}&limit=60")
            pts = [(int(x["fundingTime"]) / 1000, float(x["fundingRate"]) * 100) for x in d]
            entry = _crypto_entry(
                f"{tag} Funding Rate (perp)", "crypto_derivatives", pts, "percent",
                "Binance Futures", f"https://www.binance.com/en/futures/funding-history",
                cadence_days=0.33, as_of_ts=pts[-1][0], no_ytd=True)
            if tag in bybit_funding:
                div = (entry["value"] - bybit_funding[tag])
                entry["cross_check"] = {
                    "source": "Bybit", "value": round(bybit_funding[tag], 5),
                    "divergence_bps": round(div * 100, 2),
                }
            results[f"{tag}_funding"] = entry
            log.info(f"  Binance funding {tag:4s} → {entry['value']:+.4f}%  1D: {entry['change_1d']}")
        except Exception as e:
            log.error(f"  Binance funding {tag} failed: {e}")
            results[f"{tag}_funding"] = {"label": f"{tag} Funding Rate (perp)",
                                         "category": "crypto_derivatives", "error": str(e)}

    # ── Tier 1b: Open Interest (BTC, ETH perp), USD notional, daily history ──
    for sym, tag in SYMS:
        try:
            d = _http_get_json(f"{BINANCE}/futures/data/openInterestHist"
                               f"?symbol={sym}&period=1d&limit=60")
            pts = [(int(x["timestamp"]) / 1000, float(x["sumOpenInterestValue"])) for x in d]
            entry = _crypto_entry(
                f"{tag} Open Interest (USD)", "crypto_derivatives", pts, "price",
                "Binance Futures", "https://www.binance.com/en/futures",
                cadence_days=1, as_of_ts=pts[-1][0])
            results[f"{tag}_oi"] = entry
            log.info(f"  Binance OI {tag:4s} → ${entry['value']/1e9:.2f}B  1D: {entry['change_1d']}")
        except Exception as e:
            log.error(f"  Binance OI {tag} failed: {e}")
            results[f"{tag}_oi"] = {"label": f"{tag} Open Interest (USD)",
                                    "category": "crypto_derivatives", "error": str(e)}

    # ── Tier 1c: Long/Short account ratio (BTC, ETH) ──
    for sym, tag in SYMS:
        try:
            d = _http_get_json(f"{BINANCE}/futures/data/globalLongShortAccountRatio"
                               f"?symbol={sym}&period=1d&limit=60")
            pts = [(int(x["timestamp"]) / 1000, float(x["longShortRatio"])) for x in d]
            entry = _crypto_entry(
                f"{tag} Long/Short Ratio", "crypto_derivatives", pts, "ratio",
                "Binance Futures", "https://www.binance.com/en/futures",
                cadence_days=1, as_of_ts=pts[-1][0], no_ytd=True)
            results[f"{tag}_ls_ratio"] = entry
            log.info(f"  Binance L/S {tag:4s} → {entry['value']:.3f}  1D: {entry['change_1d']}")
        except Exception as e:
            log.error(f"  Binance L/S {tag} failed: {e}")
            results[f"{tag}_ls_ratio"] = {"label": f"{tag} Long/Short Ratio",
                                          "category": "crypto_derivatives", "error": str(e)}

    # ── Tier 2a: Deribit DVOL implied-volatility index (BTC, ETH) ──
    now_ms = int(datetime.now().timestamp() * 1000)
    start_ms = now_ms - 60 * 24 * 3600 * 1000  # 60 days back
    for cur in ("BTC", "ETH"):
        try:
            d = _http_get_json(f"https://www.deribit.com/api/v2/public/get_volatility_index_data"
                               f"?currency={cur}&start_timestamp={start_ms}"
                               f"&end_timestamp={now_ms}&resolution=43200")
            rows = d.get("result", {}).get("data", [])
            pts  = [(r[0] / 1000, float(r[4])) for r in rows]  # close
            entry = _crypto_entry(
                f"{cur} DVOL (implied vol)", "crypto_vol", pts, "index",
                "Deribit", f"https://www.deribit.com/statistics/{cur}/volatility-index",
                cadence_days=0.5, as_of_ts=pts[-1][0])
            results[f"{cur}_dvol"] = entry
            log.info(f"  Deribit DVOL {cur:4s} → {entry['value']:.1f}  1D: {entry['change_1d']}")
        except Exception as e:
            log.error(f"  Deribit DVOL {cur} failed: {e}")
            results[f"{cur}_dvol"] = {"label": f"{cur} DVOL (implied vol)",
                                      "category": "crypto_vol", "error": str(e)}

    # ── Tier 2b: Stablecoin supply (total, USD) + history → liquidity proxy ──
    try:
        d = _http_get_json("https://stablecoins.llama.fi/stablecoincharts/all")
        pts = []
        for x in d[-60:]:
            tot = x.get("totalCirculatingUSD") or x.get("totalCirculating") or {}
            usd = tot.get("peggedUSD")
            if usd is not None:
                pts.append((int(x["date"]), float(usd)))
        entry = _crypto_entry(
            "Stablecoin Supply (total)", "crypto_liquidity", pts, "price",
            "DeFiLlama", "https://defillama.com/stablecoins",
            cadence_days=1, as_of_ts=pts[-1][0])
        results["stablecoin_supply"] = entry
        log.info(f"  DeFiLlama stablecoin supply → ${entry['value']/1e9:.1f}B  1W: {entry['change_1w']}")
    except Exception as e:
        log.error(f"  DeFiLlama stablecoin supply failed: {e}")
        results["stablecoin_supply"] = {"label": "Stablecoin Supply (total)",
                                        "category": "crypto_liquidity", "error": str(e)}

    # ── Tier 2c: BTC dominance + ETH/BTC ratio (CoinGecko /global) ──
    cg_headers = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else None
    try:
        d   = _http_get_json("https://api.coingecko.com/api/v3/global", headers=cg_headers)
        mcp = d.get("data", {}).get("market_cap_percentage", {})
        btc_dom = mcp.get("btc")
        if btc_dom is None:
            raise ValueError("market_cap_percentage.btc missing")
        results["btc_dominance"] = {
            "label": "BTC Dominance", "category": "crypto_liquidity", "unit": "percent",
            "value": round(float(btc_dom), 2), "as_of": TODAY,
            "change_1d": "N/A", "change_1w": "N/A", "change_ytd": "N/A", "change_1y": "N/A",
            "history": [],
            "freshness": {"is_stale": False, "last_updated": TODAY},
            "source": "CoinGecko" + ("" if COINGECKO_API_KEY else " (keyless)"),
            "source_url": "https://www.coingecko.com/en/global-charts",
        }
        log.info(f"  CoinGecko BTC dominance → {btc_dom:.2f}%")
    except Exception as e:
        log.warning(f"  CoinGecko /global failed (Tier 2 degrades): {e}")
        results["btc_dominance"] = {"label": "BTC Dominance", "category": "crypto_liquidity",
                                    "error": str(e)}

    try:
        d = _http_get_json("https://api.coingecko.com/api/v3/simple/price"
                           "?ids=ethereum&vs_currencies=btc", headers=cg_headers)
        ethbtc = d.get("ethereum", {}).get("btc")
        if ethbtc is not None:
            results["eth_btc_ratio"] = {
                "label": "ETH/BTC Ratio", "category": "crypto_liquidity", "unit": "ratio",
                "value": round(float(ethbtc), 5), "as_of": TODAY,
                "change_1d": "N/A", "change_1w": "N/A", "change_ytd": "N/A", "change_1y": "N/A",
                "history": [],
                "freshness": {"is_stale": False, "last_updated": TODAY},
                "source": "CoinGecko", "source_url": "https://www.coingecko.com/en/coins/ethereum/btc",
            }
            log.info(f"  CoinGecko ETH/BTC → {ethbtc:.5f}")
    except Exception as e:
        log.warning(f"  CoinGecko ETH/BTC failed: {e}")

    # ── Tier 3: Spot ETF net flow — explicit not-implemented stub ──
    results["etf_net_flow"] = {
        "label": "Spot ETF Net Flow", "category": "crypto_liquidity",
        "status": "not_implemented",
        "note": "No free public API available as of 2026-06; revisit Farside/SoSoValue if they ship one.",
    }

    return results

# ─────────────────────────────────────────────────────────────────
# 7. STATE MANAGER — Continuity between daily runs
# ─────────────────────────────────────────────────────────────────

def load_prior_state() -> dict:
    """
    Load yesterday's output for delta comparisons.
    Returns empty dict if no prior state exists (first run).
    """
    state_file = DATA_DIR / "state_latest.json"
    try:
        if state_file.exists():
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            log.info(f"  Prior state loaded from: {state.get('date', 'unknown date')}")
            return state
    except Exception as e:
        log.warning(f"Could not load prior state: {e}")
    log.info("  No prior state found — this appears to be the first run")
    return {}


def save_state(output: dict) -> None:
    """
    Save a lightweight snapshot of today's key values.
    Used by tomorrow's run to compute deltas without re-fetching old data.
    """
    state_file = DATA_DIR / "state_latest.json"
    try:
        state = {
            "date":       TODAY,
            "generated":  ts_now(),
            "key_values": {},
        }

        for key, val in output.get("fred_data", {}).items():
            if isinstance(val, dict) and "value" in val:
                state["key_values"][key] = {
                    "label":    val.get("label"),
                    "value":    val.get("value"),
                    "as_of":    val.get("as_of"),
                    "category": val.get("category"),
                    "unit":     val.get("unit"),
                }

        for key, val in output.get("market_data", {}).items():
            if isinstance(val, dict) and "value" in val:
                state["key_values"][key] = {
                    "label":    val.get("label"),
                    "value":    val.get("value"),
                    "as_of":    val.get("as_of"),
                    "category": val.get("category"),
                }

        for key, val in output.get("crypto_data", {}).items():
            if isinstance(val, dict) and "value" in val:
                state["key_values"][key] = {
                    "label":    val.get("label"),
                    "value":    val.get("value"),
                    "as_of":    val.get("as_of"),
                    "category": val.get("category"),
                }

        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False, default=str)
        log.info(f"  State snapshot saved → {state_file}")

    except Exception as e:
        log.error(f"Failed to save state: {e}")

# ─────────────────────────────────────────────────────────────────
# 8. DATA QUALITY VALIDATOR
# ─────────────────────────────────────────────────────────────────

def validate_output(output: dict) -> dict:
    """
    Run all quality checks defined in Output Contract v2 Section 3.2.
    Checks: missing values, stale data, fetch errors.
    Returns: {confidence, flags, counts} for inclusion in report Section 6.
    """
    flags   = []
    missing = []
    stale   = []
    errors  = []

    all_data = {
        **output.get("fred_data", {}),
        **output.get("market_data", {}),
        **output.get("crypto_data", {}),
    }

    for key, val in all_data.items():
        if key.startswith("_"):
            continue
        if not isinstance(val, dict):
            continue
        # Explicit not-implemented stubs (e.g. ETF flow) are intentional — not a gap.
        if val.get("status") == "not_implemented":
            continue

        label = val.get("label", key)

        if "error" in val:
            errors.append(f"{label}: {val['error']}")
        elif "value" not in val:
            missing.append(label)
        else:
            freshness = val.get("freshness", {})
            if freshness.get("is_stale"):
                stale.append(f"{label}: {freshness.get('reason', 'stale')}")

    total   = len(all_data)
    n_err   = len(errors)
    n_stale = len(stale)

    # Confidence rating per Output Contract v2 Section 6
    if n_err == 0 and n_stale == 0:
        confidence = "High"
    elif n_err <= max(1, total * 0.2) and n_stale <= 2:
        confidence = "Medium"
    else:
        confidence = "Low"

    if missing: flags.append(f"[DATA MISSING] {', '.join(missing)}")
    for s in stale:  flags.append(f"[STALE] {s}")
    for e in errors: flags.append(f"[ERROR] {e}")

    return {
        "confidence":    confidence,
        "flags":         flags,
        "n_errors":      n_err,
        "n_stale":       n_stale,
        "n_missing":     len(missing),
        "total_series":  total,
    }

# ─────────────────────────────────────────────────────────────────
# 9. MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────

def main() -> dict:
    """
    Execute full data acquisition pipeline.
    Step sequence matches Output Contract v2 Section 3.1.
    """
    log.info("=" * 65)
    log.info(f"  Economic Intelligence System — Data Acquisition")
    log.info(f"  Run: {ts_now()}")
    log.info("=" * 65)

    start_time = datetime.now()

    # ── Step 1: Load prior state ────────────────────────────────────
    log.info("\n[Step 1/8] Loading prior state...")
    prior_state = load_prior_state()

    # ── Step 2: Fetch FRED (US + intl macro) ───────────────────────
    log.info("\n[Step 2/8] Fetching FRED macro data...")
    fred_data = fetch_fred()

    # ── Step 3: Fetch market prices (yfinance) ─────────────────────
    log.info("\n[Step 3/8] Fetching market prices (yfinance)...")
    market_data = fetch_yfinance()

    # ── Step 4: Fetch crypto-native quant data ─────────────────────
    log.info("\n[Step 4/8] Fetching crypto derivatives / liquidity / vol...")
    crypto_data = fetch_crypto_data()

    # ── Step 5: Fetch World Bank (supplementary) ───────────────────
    log.info("\n[Step 5/8] Fetching World Bank macro data...")
    world_bank_data = fetch_world_bank()

    # ── Step 6: Fetch economic calendar ────────────────────────────
    log.info("\n[Step 6/8] Fetching economic calendar (next 7 days)...")
    calendar = fetch_economic_calendar()

    # ── Step 7: Assemble output ────────────────────────────────────
    log.info("\n[Step 7/8] Assembling output JSON...")
    output = {
        "_meta": {
            "date":             TODAY,
            "generated_jst":    ts_now(),
            "timezone":         "Asia/Tokyo (JST, UTC+9)",
            "script_version":   "1.2.0",
            "prior_state_date": prior_state.get("date", "none — first run"),
            "output_contract":  "v2",
        },
        "fred_data":        fred_data,
        "market_data":      market_data,
        "crypto_data":      crypto_data,
        "world_bank_data":  world_bank_data,
        "calendar":         calendar,
        "prior_state":      prior_state.get("key_values", {}),
    }

    # ── Step 8: Validate quality ────────────────────────────────────
    log.info("\n[Step 8/8] Running data quality validation...")
    quality = validate_output(output)
    output["_quality"] = quality

    # ── Save raw JSON ───────────────────────────────────────────────
    out_file = DATA_DIR / f"raw_{TODAY}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    # ── Save state snapshot for tomorrow ───────────────────────────
    save_state(output)

    # ── Summary ─────────────────────────────────────────────────────
    elapsed = (datetime.now() - start_time).total_seconds()
    log.info("\n" + "=" * 65)
    log.info(f"  COMPLETE in {elapsed:.1f}s  |  Confidence: {quality['confidence']}")
    log.info(f"  Output → {out_file}")
    log.info(f"  Series: {quality['total_series']} total | "
             f"{quality['n_errors']} errors | "
             f"{quality['n_stale']} stale | "
             f"{quality['n_missing']} missing")

    if quality["flags"]:
        log.warning("\n  Quality flags:")
        for flag in quality["flags"]:
            log.warning(f"    → {flag}")
    else:
        log.info("  No quality flags — all data clean ✓")

    log.info("=" * 65)

    return output


# ─────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
