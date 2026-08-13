# Debugging session — 2026-08-07

Full engineering review of the Invoice Generator (`app.py` + all five
builder modules) plus two frontend issues surfaced afterward. Six backend
bugs fixed, one architectural gap closed, three robustness fixes, and two
real frontend bugs found and fixed with browser-verified proof.

## Financial-accuracy bugs (invoice totals didn't match what the UI reported)

1. **`app.py` — Workshop invoice subtotal ignored Parts Testing & Configuration
   charges.** The Excel workbook's own formula (`=SUM(E11:E43)`) includes rows
   34–43 (Parts Testing, driven by an uploaded FedEx Master Sheet), but
   `_finish()`'s subtotal/tax/total returned to the UI only summed Depot +
   Triage. Whenever a FedEx file was uploaded, the number shown to the user
   silently understated the real invoice total.
   **Fix:** `_run_raw_generate` now captures `part_type_totals` from
   `analyze_fedex()`; `_finish()` folds `Σ(qty × price)` into `subtotal`.

2. **`philips_builder.py` — Outbound Handling formula referenced the wrong
   column.** Live formula was `=COUNTA(Shipping!A2:A100000)` (column A =
   "Date"), but the Python-side count (used for pricing) is based on
   non-blank **Model** (column G). A row with blank Date but populated Model,
   or vice versa, would make the recalculated Excel total diverge from the
   invoiced amount. **Fix:** formula now points at column G.

3. **`amc_builder.py` — Unit Receipt formula referenced the wrong column.**
   Python's `receipt_count` = every row that passed the period date filter,
   but the formula was `COUNTA(Received!A2:...)` on "Model" (blank-able).
   **Fix:** repointed at "Receive Date" (column C), which is guaranteed
   non-blank by construction since that's the column the filter itself ran on.

## Data-correctness bugs

4. **`sanitizer.py` — `_clean_model()` suffix stripping was single-pass, not
   idempotent.** A raw model string with a stacked junk suffix (e.g.
   `"Foo-NA-2 "` — trailing space *after* `-NA-2`) only had the outer layer
   removed, leaving a malformed model name. **Fix:** loop to a fixed point
   instead of one top-to-bottom pass.

5. **`tcl_builder.py` — part group key collision across grades.**
   `key = f"part::{model}::{recv_date}"` omitted Grade, so two distinct part
   groups (e.g. A-grade and B-grade of the same model received the same day)
   generated identical keys — the frontend and `validate_breakdowns()` both
   use `key` as a dict lookup, so one group's box breakdown would silently
   get applied to both. **Fix:** included Grade in the key.

## Architectural fix

6. **Single global `_session` / `_storage_analysis` / `_philips_session` /
   `_tcl_analysis` / `_amc_session` dicts (and their SSE log queues) were
   shared by every request the process ever handled.** Two people running
   any module concurrently (or one person with two tabs) could silently
   overwrite each other's in-flight two-step analyze→confirm state.
   **Fix:** new `SessionStore` class in `app.py` — a per-module store keyed
   by a signed per-browser cookie (`_sid()`), backed by a secret persisted to
   `.flask_secret` so sessions survive app restarts. All five modules
   (Workshop, Storage, Philips, TCL, AMC) converted; `log`/`done` callbacks
   are now threaded explicitly through background-thread functions instead
   of referencing bare global queues. Verified with
   `tests/test_workshop_session_isolation.py` (real two-browser HTTP test).

## Robustness fixes

7. **Silent `except Exception: pass`** in `sanitizer._load_rules()`,
   `storage_builder.load_prices()`, and `amc_builder.load_prices()` — a
   malformed `serial_rules.json` / `storage_prices.json` / `amc_prices.json`
   fell back to defaults with zero visibility into why. **Fix:** all three
   now print a warning with the actual parse error to stderr before falling
   back.

8. **No server-side file-type validation.** Uploads were trusted by filename
   alone; a wrong file type only failed once it hit pandas/openpyxl deep
   inside the pipeline, surfacing a raw traceback. **Fix:** new
   `_save_upload()` helper (extension check + content sniff — zip-validity
   for xlsx, text-decodability for csv) wired into all ~18 upload sites
   across every module. `MAX_CONTENT_LENGTH` already covered size.

9. **New regression tests** (`tests/`, run with `pytest tests/ -v`):
   - `test_finish_subtotal.py` — locks in fix #1 (Parts Testing charges must
     be included in the subtotal returned to the UI), plus a guard that
     `_finish()` fails loudly if called without an explicit `done=` callback
     (protects the SessionStore refactor from silently falling back to some
     ambient global).
   - `test_workshop_session_isolation.py` — drives the real `/sanitize` →
     `/generate` → `/stream` HTTP flow with two concurrent test clients,
     proving fix #6: client A's invoice reflects only its own data even
     when client B runs `/sanitize` with different data in between.

## Housekeeping

- Added `.gitignore` (excludes `.flask_secret`, runtime `uploads/`/`outputs/`
  contents, editable pricing/config JSON, `__pycache__/`, `.pytest_cache/`).
- Added `requirements-dev.txt` (`requirements.txt` + `pytest`) for running
  the new test suite.

## Before deploying

- `.flask_secret` is generated automatically on first run — don't ship a
  copy of one from a dev/test environment; each real deployment should
  generate its own so session cookies can't be forged across environments.
- Run `pytest tests/ -v` (needs `requirements-dev.txt`) before shipping to
  confirm both regression tests still pass in the target environment — they
  auto-locate the sample invoice template whether it lives at
  `template/Sample_Promethean_Workshop_Invoice.xlsx` or the repo root.

---

## Addendum 1: duplicate Promethean sub-nav

The main content area had a leftover top tab-strip ("🛠 Workshop Invoice" /
"📦 Storage Invoice") duplicating the sidebar's own Promethean sub-links —
both called the exact same `showSubPage()` function. Removed the redundant
top strip from `templates/index.html` and cleaned the now-dead `subnav-*`
element references out of `static/js/app.js`'s `showSubPage()`.

## Addendum 2: root cause of the "blank TCL page" — Tailwind CDN dependency

The blank/empty-looking pages (reported for TCL, but architecturally affects
every page) were caused by `templates/index.html` loading Tailwind CSS at
runtime via `<script src="https://cdn.tailwindcss.com">`. Tailwind itself
documents this CDN script as **not for production use** — and the reason
matters here: with it, literally 100% of the app's layout, spacing, and
color styling depends on that one external request succeeding on every page
load. If it's blocked (corporate firewall, ad-blocker, offline demo, CDN
hiccup) or slow, every page renders with no styling at all — exactly the
"empty" look in the reported screenshot.

**Proof, not guesswork:** this was verified with a real headless-browser
test (Playwright), not inferred from reading the code:
1. Deliberately blocked `cdn.tailwindcss.com` and reproduced the *exact*
   blank-page symptom on TCL (and confirmed via DOM inspection that the
   underlying HTML/JS was 100% correct and present the whole time —
   `#page-tcl` was `display: block`, `visibility: visible`, with real text
   content; the JS routing logic was never the problem).
2. Compiled a static Tailwind stylesheet locally and re-tested — every page
   (Promethean Workshop + Storage, AMC, TCL, Philips, Config) rendered
   correctly and completely.
3. Final verification ran against the **actual shipped files** with *every*
   external domain blocked (not just Tailwind's CDN) — zero JS errors, zero
   failed requests, full correct rendering.

**Fix:** Tailwind is now compiled ahead of time into a static stylesheet
(`static/css/tailwind.css`) instead of fetched at runtime. A small
dev-only build pipeline lives in `build/` (`package.json` +
`tailwind.config.js`, scanning both `templates/index.html` and
`static/js/app.js` for classes) so it can be regenerated if new Tailwind
classes get added later:
```
cd build
npm install
npm run build   # regenerates ../static/css/tailwind.css
```
Node.js is only needed for that rebuild step — running the app day-to-day
needs nothing beyond the existing `requirements.txt` (Flask/pandas/openpyxl),
since the compiled CSS ships as a plain static file.

**Also fixed while investigating:** `static/logicore_mark.png` was missing
from the packaged static assets (the sidebar logo would 404). Added.

**Not changed / lower priority:** Google Fonts (`fonts.googleapis.com`) is
still loaded at runtime. Same class of risk the Tailwind CDN was, but much
lower severity — if it fails, text still renders (just with a fallback
system font) rather than the whole layout collapsing. Worth self-hosting
fonts too at some point, but not urgent.

## Install layout

This matches Flask's default `template_folder`/`static_folder`, so `app.py`
needs no manual static/template configuration for any of this:
```
invoice_app_v2/
├── app.py                        ← overwrite
├── sanitizer.py                   ← overwrite
├── storage_builder.py              ← overwrite
├── tcl_builder.py                   ← overwrite
├── philips_builder.py                ← overwrite
├── amc_builder.py                     ← overwrite
├── templates/
│   └── index.html                       ← overwrite (sidebar nav, de-duped, local Tailwind)
├── static/
│   ├── css/
│   │   ├── app.css                        ← overwrite
│   │   └── tailwind.css                    ← NEW — compiled stylesheet, no CDN dependency
│   ├── js/
│   │   └── app.js                           ← overwrite (dead subnav refs removed)
│   └── logicore_mark.png                     ← NEW — sidebar logo, was missing
├── build/                                      ← NEW — dev-only, for regenerating tailwind.css
│   ├── package.json
│   ├── tailwind.config.js
│   └── input.css
└── tests/
    ├── test_finish_subtotal.py
    └── test_workshop_session_isolation.py
```
