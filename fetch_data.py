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
import json
import logging
import traceback
from datetime import datetime, timedelta, date
from pathlib import Path

import pytz
import requests
from dotenv import load_dotenv

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

FRED_API_KEY = os.getenv("50c409688ecc9b2c76a316e956f67ef0")

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
    # ── International CB Rates (available via FRED) ───
    "ECBDFR":           ("ECB Deposit Rate",          "intl_macro", "percent"),
    "BOERUKMINLAMFSAM": ("BOE Bank Rate",             "intl_macro", "percent"),
    # Japan short-term rate (BOJ policy rate proxy)
    "IRSTCI01JPM156N":  ("BOJ Short-Term Rate",       "intl_macro", "percent"),
    # Japan CPI (relevant to Kumamoto base)
    "JPNCPIALLMINMEI":  ("Japan CPI (All Items)",     "intl_macro", "index"),
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

# Freshness thresholds (hours) — if data is older, it is flagged STALE
FRESHNESS_LIMITS = {
    "equity":     4,
    "fx":         4,
    "crypto":     4,
    "commodity":  4,
    "us_macro":   36,   # daily FRED series; monthly/quarterly exempt by design
    "intl_macro": 168,  # 7 days (CB rates change infrequently)
}

# Upcoming economic releases to track (FRED release IDs)
FRED_RELEASE_IDS = {
    82:  "Employment Situation (NFP + Unemployment)",
    10:  "Consumer Price Index (CPI)",
    46:  "Producer Price Index (PPI)",
    53:  "Gross Domestic Product (GDP)",
    175: "Consumer Confidence Index",
    18:  "Advance Monthly Retail Trade Survey",
    50:  "ISM Manufacturing PMI",
}

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


def check_freshness(data_date_str: str, category: str) -> dict:
    """
    Compare data timestamp against freshness threshold for its category.
    Returns dict: {is_stale: bool, reason: str, last_updated: str}
    """
    try:
        # Accept both date-only and datetime strings
        clean = data_date_str[:10]  # take YYYY-MM-DD portion
        data_dt = datetime.strptime(clean, "%Y-%m-%d")
        data_dt = JST.localize(data_dt)
        age_hours = (datetime.now(JST) - data_dt).total_seconds() / 3600
        limit = FRESHNESS_LIMITS.get(category, 24)

        if age_hours > limit:
            return {
                "is_stale":    True,
                "reason":      f"Data is {age_hours:.1f}h old (limit: {limit}h)",
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
            freshness = check_freshness(baselines["as_of"], category)

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

    try:
        log.info("  Downloading batch price data from Yahoo Finance...")
        raw = yf.download(
            tickers=tickers_str,
            period="400d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        log.error(f"yfinance batch download failed: {e}")
        raw = None

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
                single = yf.download(
                    ticker, period="400d", interval="1d",
                    auto_adjust=True, progress=False
                )
                close_series = single["Close"].dropna() if not single.empty else None

            if close_series is None or close_series.empty:
                log.warning(f"  {ticker}: no data available from any source")
                results[ticker] = {"label": label, "category": category, "error": "No data returned"}
                continue

            baselines = compute_baselines(close_series, unit="price")
            freshness = check_freshness(baselines["as_of"], category)

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
    Fetch upcoming major economic data releases from FRED (next 7 days).
    Requires FRED API key. Returns list sorted by date.
    Note: FRED only covers US macro releases.
    """
    if not FRED_API_KEY:
        log.warning("FRED_API_KEY missing — skipping calendar fetch")
        return [{"error": "FRED_API_KEY required for calendar fetch"}]

    upcoming   = []
    today_str  = date.today().isoformat()
    end_str    = (date.today() + timedelta(days=7)).isoformat()

    for release_id, release_name in FRED_RELEASE_IDS.items():
        try:
            url = (
                f"https://api.stlouisfed.org/fred/release/dates"
                f"?release_id={release_id}"
                f"&realtime_start={today_str}"
                f"&realtime_end={end_str}"
                f"&api_key={FRED_API_KEY}"
                f"&file_type=json"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            for entry in data.get("release_dates", []):
                release_date = entry.get("date", "")
                if today_str <= release_date <= end_str:
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
    log.info(f"  Calendar: {len(upcoming)} major US releases in next 7 days")
    return upcoming

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
    }

    for key, val in all_data.items():
        if key.startswith("_"):
            continue
        if not isinstance(val, dict):
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
    log.info("\n[Step 1/7] Loading prior state...")
    prior_state = load_prior_state()

    # ── Step 2: Fetch FRED (US + intl macro) ───────────────────────
    log.info("\n[Step 2/7] Fetching FRED macro data...")
    fred_data = fetch_fred()

    # ── Step 3: Fetch market prices (yfinance) ─────────────────────
    log.info("\n[Step 3/7] Fetching market prices (yfinance)...")
    market_data = fetch_yfinance()

    # ── Step 4: Fetch World Bank (supplementary) ───────────────────
    log.info("\n[Step 4/7] Fetching World Bank macro data...")
    world_bank_data = fetch_world_bank()

    # ── Step 5: Fetch economic calendar ────────────────────────────
    log.info("\n[Step 5/7] Fetching economic calendar (next 7 days)...")
    calendar = fetch_economic_calendar()

    # ── Step 6: Assemble output ────────────────────────────────────
    log.info("\n[Step 6/7] Assembling output JSON...")
    output = {
        "_meta": {
            "date":             TODAY,
            "generated_jst":    ts_now(),
            "timezone":         "Asia/Tokyo (JST, UTC+9)",
            "script_version":   "1.0.0",
            "prior_state_date": prior_state.get("date", "none — first run"),
            "output_contract":  "v2",
        },
        "fred_data":        fred_data,
        "market_data":      market_data,
        "world_bank_data":  world_bank_data,
        "calendar":         calendar,
        "prior_state":      prior_state.get("key_values", {}),
    }

    # ── Step 7: Validate quality ────────────────────────────────────
    log.info("\n[Step 7/7] Running data quality validation...")
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
