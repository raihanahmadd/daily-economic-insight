# Changelog

All notable fixes and enhancements to the Daily Economic Intelligence Report pipeline.

---

## Round 1 — Empty report & squished charts

**`fetch_data.py`**
- **FRED key never loaded:** `os.getenv("<key-value>")` → `os.getenv("FRED_API_KEY")`. The
  key was pasted as the *variable name*, so FRED (rates + calendar) was always skipped.
- **Yahoo Finance returned nothing:** added retry + `threads=False` to avoid connection-pool
  exhaustion / 429 rate-limiting.
- **30-day trend charts were always empty:** `compute_baselines` now stores a `history` array
  (last 60 readings) — the renderer needs it but it was never written.
- **Windows cp932 crash:** force `stdout`/`stderr` to UTF-8 (couldn't encode `✓ ⚠` glyphs).
- **Dead FRED series:** BOE `BOERUKMINLAMFSAM` → `IUDSOIA` (SONIA); removed discontinued Japan CPI
  (covered by World Bank).

**`render_report.py`**
- **Charts "gepeng" (squished) in PDF:** every image was forced to 16.5×7.4 cm; now each image
  keeps its native aspect ratio (via Pillow), capped to a bounding box.
- **`UnicodeEncodeError` crash:** force UTF-8 stdout/stderr.
- **`S&P; 500` artifact:** escape bare `&` before reportlab Paragraphs.

**`requirements.txt`** — added `curl_cffi`.

---

## Round 2 — yfinance version incompatibility

**`fetch_data.py`**
- **`'str' object has no attribute 'name'`:** yfinance `0.2.54` crashed against newer `curl_cffi`
  when fetching a fresh Yahoo crumb. **Fix:** upgrade to **`yfinance >= 1.4.1`** and *stop passing
  a custom session* — yfinance now bundles `curl_cffi` and impersonates a browser internally.
- **Freshness redesign:** daily bars (dated midnight) were compared to a 4-hour limit, so live data
  was always flagged "stale" → permanent "Low" confidence. Now `check_freshness` works in **days vs.
  each series' inferred update cadence** (`cadence_days`); confidence reaches **High** when clean.

**`requirements.txt`** — `yfinance==0.2.54` → `yfinance>=1.4.1`.

---

## Round 3 — Calendar, per-chart key points, conclusion

**`fetch_data.py`**
- **Forward calendar was empty:** FRED `/release/dates` needs
  `include_release_dates_with_no_data=true` (+ `realtime_end=9999-12-31`, `sort_order=asc`) to
  surface *future* scheduled dates. Window widened to 14 days.
- **Wrong release IDs:** verified against FRED's `/release` endpoint and corrected
  (50=Employment Situation, 10=CPI, 54=PCE, 46=PPI, 53=GDP, 9=Retail Sales, 11=ECI).
  ISM PMI / Consumer Confidence removed (not published on FRED).

**`render_report.py`**
- **Per-chart key points in PDF:** each chart now renders its "What this chart shows / Key
  Observations / Watch for" text beneath the image (previously image-only).
- **New Section 7 "Key Takeaways":** `auto_conclusion()` distills the day's most important points.
- **Embedded DejaVu Sans font:** so `↑ ↓ ⚠ ⚡` and en/em dashes render instead of tofu boxes.
- **Honest calendar message:** distinguishes "no FRED key" from "no releases scheduled."

---

## Round 4 — Repository setup

- Added `README.md`, `CHANGELOG.md`, `.gitignore`, and `.env.example`.
- `.gitignore` excludes the secret `.env`, the virtualenv, local settings, and regenerated
  `logs/` + `data/`.
