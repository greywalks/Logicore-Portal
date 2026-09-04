# Logicore Portal — Project Brief
**Version:** v24
**Last Updated:** August 2026
**Purpose:** This file gives a new Claude session everything it needs to extend this project without requiring data files. Read this before touching any code.

**v24 change — Portal restructure + Training Tracker mount:** This repo is no longer just the invoice generator — it's now the **Logicore Portal**, a Flask shell with a top-level sidebar tab picker. The entire previous app (all five invoice clients + Config) now lives inside the **Invoice Generator** portal tab, unchanged in behavior. Alongside it: **SMS NonConforming** and **TBD 2** (static placeholder tabs, no backend yet), and **Training Tracker** — a real, separate Flask app (`training_tracker/app.py`, cloned from the standalone `training-planner` project) mounted at `/training-tracker/` via `werkzeug.middleware.dispatcher.DispatcherMiddleware`. See "Portal shell" and "Training Tracker" sections below for the full picture before touching top-level nav or the training_tracker/ folder.

**Also folded into v24 — folder reorg.** The repo root used to be flat (`index.html`, `app.js`, `app.css`, `*.xlsx` templates all sitting loose next to `app.py`). It's now organized: `templates/index.html`, `static/js/app.js`, `static/css/{app.css,tailwind.css}`, `static/{logicore_mark.png,logicore_logo.png}`, `template/*.xlsx` (Excel templates the builders copy-and-fill), `build/` (Tailwind CLI tooling — `package.json`, `tailwind.config.js`, `input.css`; not needed to run the app). No code changes were needed for this — every builder already referenced `template/<file>.xlsx` via `Path(__file__).parent / "template" / ...`, and `templates/index.html` already used root-relative `/static/...` URLs, so moving files onto disk to match was purely mechanical.

---

## Architecture Overview

This is a **Flask web app** that runs locally on the user's machine (launched via `Launch_Invoice_Generator.bat`, app version reported at `/version` — currently `APP_VERSION = "9.0"` in `app.py`, unrelated to this doc's own version number above). It's now the **Logicore Portal**: a sidebar tab picker wrapping the invoice generator (five USSI client accounts — Promethean 3 modules, AMC, TCL, Philips) plus a real Training Tracker mount and two placeholder tabs.

```
portal/
├── app.py                  # Flask routes — invoice modules + Training Tracker mount (see below)
├── builder.py               # Excel builder for Workshop Invoice
├── storage_builder.py       # Excel builder for Storage Invoice
├── fedex_shipment_builder.py # Builder for FedEx Shipment Upload (Promethean, not an invoice — see Module 3)
├── amc_builder.py            # Excel builder for AMC Warehouse Invoice
├── tcl_builder.py             # Excel builder for TCL Warehouse Invoice
├── philips_builder.py         # Excel builder for Philips Warehouse Invoice
├── sanitizer.py              # Data cleaner / classifier for Workshop raw production files
├── normalizer.py             # Canonicalizes "Result" text (harvest/scrap descriptions) for display
├── serial_rules.json         # Config-driven serial-prefix → model/size mapping (hot-reloadable, editable in-app)
├── storage_prices.json       # Optional — created at runtime when prices are overridden in-app (not in repo by default)
├── fedex_shipment_defaults.json # Config-driven site info + margin divisor for the FedEx Shipment Upload module
├── whitelist_default.json    # Built-in Parts/Units whitelist fallback for Storage module
├── templates/index.html      # Portal shell + entire Invoice Generator tab (Tailwind, vanilla JS)
├── static/
│   ├── js/app.js              # All frontend JS — portal nav, page/sub-page nav, every module's logic
│   ├── css/app.css            # Hand-written theme/layout CSS (design tokens, sidebar, cards, etc.)
│   ├── css/tailwind.css       # Compiled Tailwind output (see build/) — do not hand-edit
│   └── logicore_mark.png, logicore_logo.png
├── build/                     # Dev-only Tailwind build tooling for templates/index.html + static/js/app.js
│   ├── package.json
│   ├── tailwind.config.js     # Must be this exact filename (dotted) — the CLI won't discover `tailwind_config.js`
│   └── input.css
├── template/                 # Excel/template files (one per invoice module)
│   ├── Sample_Promethean_Workshop_Invoice.xlsx
│   ├── Sample_Promethean_Storage_Small_Parts_Invoice.xlsx
│   ├── TCL_Warehouse_Invoice_Template.xlsx
│   └── FedEx_Shipment_Upload_Template.xlsx   # header-only copy of the ticketing system's own import template
├── uploads/                  # Temp storage for user-uploaded files (Invoice Generator only)
├── outputs/                  # Generated invoices land here (Invoice Generator only)
├── training_tracker/          # Separate mounted Flask app — see "Training Tracker" section below
│   ├── app.py                 # Full app (routes, DB, auth) — same code as the standalone training-planner repo,
│   │                           # minus its own __main__ server startup and static folder (portal supplies both)
│   ├── templates/              # base.html, login.html, index.html, week_detail.html, people.html, sign.html,
│   │                           # admin.html, account.html, reports.html — retthemed to match the portal (see below)
│   ├── training_planner.db    # Created automatically on first run, next to training_tracker/app.py
│   └── .secret_key            # Auto-generated session-signing key for Training Tracker — keep private
├── requirements.txt           # flask, pandas, openpyxl, waitress, reportlab (waitress+reportlab are Training Tracker's)
└── Launch_Invoice_Generator.bat
```

### Portal shell (new in v24 — read this before touching top-level nav)

`templates/index.html` now has **three levels of navigation**, not two:
- **Portal-level tabs** (`showPortalPage(name)` in `static/js/app.js`): `invoice-generator` (default — contains the entire previous app, unchanged), `sms-nonconforming` (placeholder), `tbd2` (placeholder). Toggles `hidden` on `<div id="portal-content-<n>">` (main content) and `<div id="portal-body-<n>">` (sidebar sub-nav, `invoice-generator` only), and `active`/`text-steel` on `<button id="portal-nav-<n>">`.
- **Training Tracker is NOT one of these three.** Its sidebar entry (`<a id="portal-nav-training-tracker" href="/training-tracker/">`) is a plain link that does a real page navigation, not a JS-toggled tab — it's a separate Flask app with its own login/session flow (see below), and doesn't fit the hidden-div SPA pattern the other tabs use. Don't add it to `showPortalPage()`'s `allPortals` array; it was deliberately left out.
- **Top-level client tabs**, inside `invoice-generator` (`showPage(name)`): `promethean`, `amc`, `tcl`, `philips`, `config` — unchanged from before.
- **Sub-nav within a client tab** (`showSubPage(name, client)`): currently only `promethean` has one (`invoice`, `storage`, `fedex-shipment`) — unchanged from before.

**To add a new portal-level tab** (a sibling of Invoice Generator, like SMS NonConforming): add a `<button class="side-link portal-nav-btn text-steel" data-portal="<name>" id="portal-nav-<name>" onclick="showPortalPage('<name>')">` in the sidebar, a `<div id="portal-content-<name>" class="hidden ...">` in main content, and register `<name>` in the `allPortals` array near the top of the "Sidebar / topbar enhancements" IIFE in `static/js/app.js`. If the new tab needs real multi-page functionality (its own auth, its own DB) rather than a simple form, follow the Training Tracker pattern instead (separate Flask app + `DispatcherMiddleware` mount + plain `<a href>` sidebar link) — don't force it into the hidden-div pattern.
**To add a new client inside Invoice Generator:** unchanged from before — new top-level page (`nav-<client>`/`page-<client>`, registered in `showPage()`'s `allPages` array) inside `portal-content-invoice-generator`.

### Training Tracker (new in v24)

A weekly training planner (assign/edit topics per week, attendance, digital + physical sign-off sheets, roster, reports) — originally built and shipped as its own standalone repo (`training-planner` on GitHub), now folded into the portal as a mounted sub-app rather than rewritten as a blueprint of the invoice generator.

**Why mounted, not merged as a blueprint:** Training Tracker has real multi-page navigation, its own login/session/role system (admin/editor/trainee), and its own SQLite database — none of which fit the Invoice Generator's single-page-with-hidden-divs pattern, and rewriting ~1700 lines of routes as a blueprint would have been pure risk for no benefit. Instead, `training_tracker/app.py` is a complete, ordinary Flask app (`app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=None)`) that runs *exactly* as it did standalone. The portal's `app.py` composes it in at the WSGI level:
```python
from training_tracker.app import app as training_tracker_app, init_db as _tt_init_db
_tt_init_db()
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {"/training-tracker": training_tracker_app.wsgi_app})
```
`DispatcherMiddleware` (from `werkzeug.middleware.dispatcher`) sets `SCRIPT_NAME` correctly on every request routed to it, so `url_for(...)` calls inside `training_tracker/app.py`'s own routes/templates automatically produce `/training-tracker/...`-prefixed URLs with zero changes to that code. `_tt_init_db()` is called at **import time** here (not left inside `training_tracker/app.py`'s `if __name__ == "__main__":` guard, since that guard never runs when the module is imported rather than executed directly) — `init_db()` itself is idempotent (`CREATE TABLE IF NOT EXISTS` everywhere), safe to call on every portal startup.

**Static assets:** `training_tracker/app.py` has `static_folder=None` — its templates reference the portal's shared assets directly via root-relative paths (`<img src="/static/logicore_mark.png">` in the header, for instance), not its own static endpoint. Since the browser resolves those paths against the domain root regardless of which mounted app served the page, they correctly hit the *portal's* static route. Don't add a `static_folder` back without checking this still holds.

**Data:** `training_tracker/training_planner.db` and `training_tracker/.secret_key` are created next to `training_tracker/app.py` on first run (via `BASE_DIR = os.path.dirname(os.path.abspath(__file__))` inside that file, unchanged from the standalone version) — separate from the invoice generator's own data, no shared state between the two apps beyond the WSGI-level mount. Default login is `admin` / `admin`; there's a red "using default admin password" banner in the app itself reminding whoever sets it up to change it.

**Retheme (v24):** The standalone `training-planner` shipped with its own warm "parchment/ink/amber" paper theme (light page, Fraunces serif headings, amber accent) and a fully admin-configurable color system already built in (`THEME_FIELDS`/`THEME_DEFAULTS` in `training_tracker/app.py`, edited from `/training-tracker/admin` → Appearance tab, stored in the `theme_settings` table, injected into `base.html`'s Tailwind CDN config at request time). Rather than add a new color system, the existing one's **hex defaults were repointed to the portal's own dark palette** (see `static/css/app.css` `:root` — `--bg`, `--accent`, etc.) and — critically — **the light/dark derivation direction in `build_theme()` was flipped**:
- `ink` (originally the dark tone, used for text-on-light-page + the header's dark bg) is now the **light** tone (`#EEF2F6`, matches the portal's `--text`), so its "faint"/muted variant is derived with `darken()` instead of `lighten()`.
- `parchment` (originally the light tone, the page background) is now the **dark** tone (`#0A0D12`, matches the portal's `--bg`), so its "dim"/"line" (card surface / border) variants are derived with `lighten()` instead of `darken()`.
- `amber` (accent) → portal teal `#2FD8A6`; `sage` (success) → `#22C55E`; `rose` (danger) → `#EF4444`. These two keep their original `darken()`/`lighten()` derivation — only ink/parchment's direction changed, since only they flipped which tone (light vs dark) they represent.
- Every hardcoded `bg-white` card across the templates (44 occurrences) was swapped to `bg-parchment-dim` (the new dark card-surface token) so they pick up the theme dynamically instead of staying a hardcoded white square on a dark page. Every `bg-ink ... text-parchment` "dark pill" button (the original theme's primary-button pattern) was swapped to `bg-amber ... text-ink` — light-on-dark became light-pill-dark-text, matching the portal's own `bg-accent text-ink` primary-button convention.
- The header in `base.html` is **not** theme-token-driven — it's pinned to a fixed `.tt-header { background: #080a0e; color: #eef2f6; }` (matching the portal's `--sidebar-bg`/`--text` exactly) so it stays a constant dark bar regardless of what an admin sets ink/parchment to in Appearance settings, and links back to the portal via the Logicore mark. If you ever want the header to be admin-themeable too, it'll need its own THEME_FIELDS entry rather than reusing ink/parchment (which now drive the main canvas).
- All seven `<dialog>` modal backdrops (`backdrop:bg-ink/60`) were changed to `backdrop:bg-parchment/70` — they need to stay a *dark* scrim regardless of the ink/parchment swap, and after the swap `ink` is light so the original class would have produced a translucent light overlay instead.
- Fonts: `Fraunces` (serif display font) → `Space Grotesk` (matches the portal's `--font-display`); `Inter` and `IBM Plex Mono` were already shared with the portal, unchanged.
- **Why still on the Tailwind CDN script** (`<script src="https://cdn.tailwindcss.com">`) instead of the portal's precompiled `static/css/tailwind.css`, even though the rest of the portal deliberately avoids the CDN for reliability: the Appearance admin panel needs to inject admin-chosen hex values into Tailwind's color config *at request time*, per-instance, from the database — a build-time-compiled stylesheet can't do that. This is a deliberate, narrow exception for this one sub-app, not a precedent for the rest of the portal.
- `site.name` content default is still `"Training Ledger"` (admin-editable from `/training-tracker/admin` → Edit Content, unchanged from the standalone repo) even though the portal sidebar label is "Training Tracker" — these don't have to match; the sidebar label is fixed portal chrome, the in-app name is whatever the admin sets it to.

If you're extending Training Tracker's functionality (not just its look), treat `training_tracker/app.py` as if you were working in the standalone `training-planner` repo — its routes, DB schema, and CONTENT_SCHEMA/THEME_FIELDS system are all unchanged from that repo except for the mounting/theming changes documented above.



### How a module works (pattern to follow for new modules)
Most modules use a **two-step workflow**, not a single generate call — but it's not mandatory. Workshop and Storage need it because their source data has real ambiguity (unresolved serials, unmatched parts) that needs a human decision before billing. **FedEx Shipment Upload (Module 3, new in v23) also uses two steps, but for a lighter reason**: the transform itself is fully deterministic (nothing to correct), the review step exists purely so a bad upload or an unexpected batch of "defaulted to USSI's own office" rows surfaces before anything is written to `outputs/`, given this bills money. Don't assume two-step always means "there's data to reconcile" — check whether the specific module actually needs a correction UI before copying the review-table pattern wholesale; a simpler module (like this one) can skip straight to a summary + "used defaults" list.

1. **Step 1 route in `app.py`** — POST that uploads files, processes/classifies everything server-side, and returns a JSON summary + issues/review lists for the user to confirm (`/sanitize` for Workshop, `/analyze_storage` for Storage, `/analyze_fedex_shipment` for FedEx Shipment Upload). No file is written yet.
2. **Step 2 route** — POST that accepts the user's corrections/confirmations, spawns a background thread that builds the actual Excel file, and returns `{ok: true}` immediately (`/generate` for Workshop, `/confirm_storage` for Storage, `/build_fedex_shipment` for FedEx Shipment Upload).
3. **SSE stream** — a separate `GET /stream[_module]` endpoint streams `{type: "log"|"ping"|"done"}` messages from a `queue.Queue` while the background thread runs.
4. **Builder file** — `<module>_builder.py` contains the pure Python logic: reads source files, processes data, calls openpyxl to write the Excel output. Takes a `log=` callback for SSE messages.
5. **Template Excel file** — Placed in `template/`. The builder does `shutil.copy(template, output)` then opens and rewrites sheets with openpyxl.
6. **Frontend section in `index.html`** — a `<div id="page-<module>">` (or a sub-page under a client tab) with file upload drop-zones, a form, a log box, and a result card.
7. **Per-session state** — a module-level `_module = SessionStore()` instance (see `SessionStore` near the top of `app.py`), not a bare dict — keeps concurrent browser sessions from overwriting each other's in-progress analysis. Every module does this, including the new one (`_fedex_shipment = SessionStore()`).

---

## Module 1: Workshop Invoice

**Client tab / sub-nav:** `promethean` → "🛠 Workshop Invoice" (`page-invoice`)
**Routes:** `POST /sanitize` (step 1) → `POST /generate` (step 2) → `GET /stream` (SSE)
**Legacy path:** `/generate` also accepts `mode=legacy` for pre-sanitized `Repair_Data.xlsx` uploads (skips `/sanitize`, calls `process_legacy()` directly).
**Template:** `Sample_Promethean_Workshop_Invoice.xlsx`
**Builder:** `builder.py` (uses `normalizer.py` to canonicalize `Result` text for the Depot Repair / Triage Units sheets)

### Input Files
| File | Type | Purpose |
|------|------|---------|
| Raw Production File | `.xlsx` | Sheet: `Repair Data`. Contains all repair records. |
| Previously Invoiced Master | `.xlsx` | Sheets: `Repair Log`, `Triage Log`. Used for dedup. |
| Shipping History | `.csv` | Column: `Serial Number`, `Shipped Date` (`MM-DD-YYYY`). Determines if a dup was reshipped. |
| FedEx Master Sheet | `.xlsx` | **New (v20).** `Outbound Tracking`, `Request Date`, `MSO`, `Part/Component Reported Product Code`, `Serial Number`, `Quantity`. Drives the "Parts Testing & Configuration" Breakdown section — this charge category moved here from the Storage invoice. Same file/format as Storage's FedEx upload; the two modules are uploaded and processed independently even though the source data usually overlaps. Optional — if omitted, that section is left blank. |

> **v22 fix — frontend upload zone was missing.** The backend (`/sanitize` reading `request.files.get("fedex")`) and the JS (`setupZone('zone-fedex2', 'file-fedex2', ...)` in `app.js`) were both already wired for this v20 field, but the actual `<label class="drop-zone">` / `<input name="fedex">` markup was never added to `templates/index.html`'s Raw Production File upload section — so there was no way to select the file from the UI, and Parts Testing & Configuration silently stayed blank on every raw-flow invoice. Added the drop-zone (`id="zone-fedex2"` / `id="file-fedex2"`, `name="fedex"`, marked optional to match backend behavior) right after the Shipping History zone, inside `sanitize-form`, so it's picked up automatically by the existing `new FormData(form)` submit handler. No JS or backend changes were needed — only the missing HTML. The Legacy (pre-sanitized) tab intentionally has no FedEx zone; that path never supported Parts Testing & Configuration.

### Two-step flow
1. **`/sanitize`** — Loads `Repair Data`, filters to the date range, derives `Actual Model`/`Size` from the serial number (via `serial_rules.json` + hardcoded fallback prefixes in `sanitizer.py`), derives `Type`/`Type2`, and flags issues (`unresolved_size`, `suspect_serial`, `unknown_category`) plus a separate `auto_corrections` list (silent model/size fixes it already applied) for UI transparency. Also uploads and stashes the FedEx Master Sheet path in session (`sess["fedex_path"]`) — not analyzed yet, just saved. Returns everything as JSON; nothing is written to disk yet. Session state (parsed dataframes) is cached server-side in a global `_session` dict.
2. **`/generate`** — User resolves issues via `corrections` (a dict of `{row_index: {field, value}}`, or `"EXCLUDE"`), then the server re-filters, dedups against the Previously Invoiced Master, runs `storage_builder.analyze_fedex()` against the FedEx file for this billing period, builds the invoice via `builder.build()`, **exports a corrected copy of the raw production workbook** (`Promethean_Production_Corrected_<ts>.xlsx`), and **writes an updated Previously Invoiced Master** (`Previously_Invoiced_Master_Updated_<ts>.xlsx`, with this run's units appended to `Repair Log`/`Triage Log`) so it's ready for next month. All three filenames come back in the SSE `done` payload.

### Key data logic
- **Serial → Model/Size**: `sanitizer._serial_to_model_and_size()` first checks `serial_rules.json` (longest-prefix-wins, config-driven, hot-reloadable and editable from the in-app Config page), then falls back to a large hardcoded prefix table for legacy product lines (AP6, AP7 U/B-series, AP9-A/B, AP10-A/B, APLE, APLX, VTP). A few prefixes (`9A75` with `V` marker, `9B75GP`) get special-cased overrides even when a config rule matches.
- **-02 revision suffix**: some product lines add `-02-` to the model name based on the *year character* embedded in the serial (position 5 or 6 depending on the rule) — `L` (2021) or later triggers it. Rules can also force `always` or `never`. This is editable per-prefix in the Config page.
- **Type** (from `Category` column, substring match): contains `"scrap"` or `"refurbished"` → `Depot Repair Tab`; contains `"pending parts"` → `Triage Tab`.
- **Type2** (repair complexity): `Category` containing `"scrap"` → `Salvage of Hardware and Scrap` (regardless of Result). Otherwise, `Result` text is checked against `HEAVY_KEYWORDS` (lcd/lcm/deflector/overlay variants) → `Heavy`, else → `Basic`.
- **Size**: `86` = Large; `55`/`65`/`70`/`75` = Small and billable. Resolved in priority order: explicit `Derive Size` value → a small typo-correction map (`5t→75`, `6t→86`, etc.) → derived from the serial number → parsed out of the clean model name as a last resort. Rows still unresolved become an `unresolved_size` issue.
- **Dedup** (`apply_dedup()` in `app.py`): a serial already in the Previously Invoiced Master is excluded *unless* it shipped on/after that prior invoice date. Master entries are cut off at the current billing period start (so the current run doesn't cancel itself out), **except** for the "previously triaged" discount lookup, which intentionally uses the *full, unfiltered* triage history (including the current period) — a unit triaged this month and repaired this month still gets the discount. A serial that was **only** triaged before (never repaired) is *not* treated as a duplicate when it now shows up as a completed repair — that's legitimate new billing, not a resubmission.
- **Previously Triaged discount**: units appearing anywhere in the Triage Log master before/at time of repair get the lower repair-only rate (`was_prev_triaged=True`).

### Pricing (`PRICES` dict in `app.py`)
| Type | Size | Previously Triaged | Price |
|------|------|--------------------|-------|
| Depot Repair / Basic | Small | No | $110 |
| Depot Repair / Basic | Large | No | $135 |
| Depot Repair / Heavy | Small | No | $220 |
| Depot Repair / Heavy | Large | No | $268 |
| Depot Repair / Basic | Small | Yes | $64 |
| Depot Repair / Basic | Large | Yes | $74 |
| Depot Repair / Heavy | Small | Yes | $108 |
| Depot Repair / Heavy | Large | Yes | $127 |
| Depot Repair / Salvage of Hardware and Scrap | Any | Any | $28 |
| Triage / Basic | Small | — | $86 |
| Triage / Basic | Large | — | $101 |
| Triage / Heavy | Small | — | $152 |
| Triage / Heavy | Large | — | $181 |

> **Note:** the Breakdown sheet template (`builder.py`) also has three static line items **not** driven by `PRICES` / computed data — they exist as invoice lines but are hardcoded to qty 0 unless someone manually edits the output: **Reboxing Fee** ($50/each), **Immediate Scrap** ($19/each, keyed off a `"Immediate Scrap"` label that the current classification logic never actually produces), **Special Warehouse Projects** ($75/hour). Worth knowing about if a future change needs to wire real data into them.

### Parts Testing & Configuration (moved here from Storage invoice in v20)
As of v20, this whole charge category bills on the **Workshop** invoice, not Storage — Storage still receives the FedEx file and still shows a read-only copy of the same sheet, but no longer sums it into its own Breakdown. Source: the FedEx Master Sheet upload (see Input Files above), run through the *same* `storage_builder.analyze_fedex()` / `classify_part_type()` logic the Storage module uses, so pricing stays centrally config-driven (`storage_prices.json`, editable on the Config page) regardless of which invoice bills it.

Unlike the repair/triage lines above, these Breakdown rows (33–43) don't get their quantity from a Python-computed value — they're **live Excel `SUMIFS` formulas** against a `TblPartTesting` table on the Workshop invoice's own "Part Testing & Programming" sheet (written by `storage_builder._build_part_testing()`, reused from the Storage module so both invoices render identical data from the same FedEx source). See Module 2 below for the part-type → price mapping (`PART_TYPE_MAP` / `DEFAULT_PART_TYPE_PRICES` in `storage_builder.py`) — it's the same table, just consumed by a different invoice now.

Tax rate: **7%** (hardcoded `TAX_RATE = 0.07` in `app.py`, also baked into an Excel formula `=E45*7%`).

### Breakdown row layout (v20)
Rows 11-14 Triage & Repair · 16-19 Triage-only · 21-24 Repair-only (previously-triaged discount) · 26 Reboxing · 28-29 Scrap/Salvage · 31 Special Warehouse Projects · **33 Parts Testing & Configuration header, 34-43 part-type lines** · totals at **45 (Subtotal, `=SUM(E11:E43)`) / 46 (Tax) / 47 (Total)**. The totals rows shifted down from 33-35 in earlier versions to make room for the new section — if you're diffing against an old export, don't assume row 33 still means "Subtotal."

### Output Excel sheets
- `Breakdown` — Summary with address block, line items, subtotal/tax/total
- `Depot Repair` — One row per repaired unit (Model, Serial, Type label, Size, Repair Cost, Parts Summary via `normalize_result()`)
- `Triage Units` — One row per triaged unit (Model, Serial, Triage/Result, Derived Type, Size, Cost)
- `Part Testing & Programming` — **New (v20).** Same columns/data as Module 2's sheet of the same name (`MSO`, `Request Date`, `Outbound Tracking`, `Part #`, `Type`, `Serial`, `Quantity`, `Individual Part Fee`, `Total Programming Fee`, `Part Pick Fee`), written by the shared `storage_builder._build_part_testing()`. Its `TblPartTesting` Excel Table is what the Breakdown's Parts Testing SUMIFS formulas read from — don't rename the table or the sheet without updating those formulas too.
- `Excluded Serials` — One row per serial that `apply_dedup()` pulled out as a duplicate (previously invoiced and either never shipped again, or shipped before that prior invoice and not reshipped since). Columns: `Model`, `Serial`, `Source Tab` (`Depot Repair`/`Triage Units`), `Category`, `Result`, `Original Date`, `Reason` (plain-English, includes the prior invoice date and, when relevant, the last shipped date). Written even when nothing was excluded (placeholder row). Built in `apply_dedup()` (returns a 3rd value: `depot_clean, triage_clean, excluded_df`), rendered by `builder._build_excluded()`.
  > **Scope note:** this tab currently only covers the dedup step. It does **not** yet cover rows silently dropped earlier in the pipeline — e.g. unresolved `sanitizer` issues (bad serial, unknown category, unresolved size) that were never corrected, or rows the user manually marked `EXCLUDE` during correction. Those are visible in the pre-generate review UI but don't currently get an audit-trail row in the output workbook. Worth a follow-up if full elimination coverage is needed.

---

## Module 2: Storage & Small Parts Invoice

**Client tab / sub-nav:** `promethean` → "📦 Storage Invoice" (`page-storage`)
**Routes:** `POST /analyze_storage` (step 1) → `POST /confirm_storage` (step 2) → `GET /stream_storage` (SSE)
**Template:** `Sample_Promethean_Storage_Small_Parts_Invoice.xlsx`
**Builder:** `storage_builder.py`

> **Shared with Module 1 (v20):** `storage_builder.analyze_fedex(fedex_path, period_start, period_end, part_prices, small_part_pick_price, log)` is the one place FedEx Master Sheet parsing/classification happens — both `analyze_storage()` here and `app._analyze_fedex_for_workshop()` (Workshop) call it. If you change FedEx parsing logic, you're changing it for both invoices at once; that's intentional, but worth knowing before you "fix" something for one module and break the other's numbers.

### Input Files
| File | Type | Columns of Interest |
|------|------|---------------------|
| Inventory Export | `.csv` | `Model`, `Serial Number`, `Item Type` (`Unit`/`Part`) |
| Shipping History | `.csv` | `Ticket Number` (→ MSO), `Pickup Date` (`MM-DD-YYYY`, **this is the field that determines billing period**, not "Shipped Date"), `Model`, `Serial Number`, `Tracking Number`, `Sales Order Number` |
| Receipt Log | `.csv` | `Received Date` (`MM-DD-YYYY`), `Model`, `Serial Number` (holds RMA when it's a part), `Item Type` (`="Unit"` or blank = part) |
| FedEx Master Sheet | `.xlsx` | `Outbound Tracking`, `Request Date`, `MSO`, `Part/Component Reported Product Code`, `Serial Number`, `Quantity` |
| Parts & Units Whitelist | `.xlsx`, optional | Sheet `Parts`: `Parts List` column. Sheet `Units`: multiple model-number columns. **If not uploaded, falls back to `whitelist_default.json`** (built-in parts list + unit models). |

> **Note:** CSV files from the system use Excel-style quoting (`="value"`). All CSV reads must strip this — `clean_csv_df()` in `storage_builder.py` cleans **both column names and cell values**.

### Two-step flow
1. **`/analyze_storage`** — uploads files, runs `analyze_storage()`, which computes everything and also produces **review buckets** for the user to see before committing:
   - `auto_spc_rows` — Small Parts Check-In lines that were **auto-approved** (whitelist match **and** a valid RMA in the `Serial Number` column, pattern `^M\d{8}$` — exactly "M" + 8 digits, case-insensitive).
   - `unmatched_df` — everything that did *not* auto-qualify, tagged by reason: `WL Part – No Valid RMA` (on the parts whitelist but missing/malformed RMA), `Non-Whitelist Part`, `Non-Whitelist Unit` (received unit whose model isn't on the unit whitelist). All original Receipt Log columns are preserved per row.
   The route returns counts (`auto_spc_count`, `unmatched_count`, etc.) and stashes the full analysis + invoice metadata in a server-side `_storage_analysis` dict.
2. **`/confirm_storage`** — reads back `_storage_analysis`, spawns a background thread that calls `build_storage_invoice()` to write the Excel file. (The route body is currently reserved for future manual-inclusion params — as of this version, whatever was auto-approved in step 1 is what gets billed; there isn't yet a UI path to promote an unmatched row into the invoice.)

### Data logic per line item
**Unit Storage** (`$8/unit`, key `unit_storage`)
= All Inventory Export rows where `Item Type == "Unit"` (deduped by serial)
PLUS units shipped this period (`Pickup Date` filled in, within the billing window) whose serials are NOT already in inventory
→ combined, deduped by serial.

**Pallet Storage** (`$23.50/pallet`, key `pallet_storage`)
= Manually entered by user at generation time (`pallet_count` form field). No file source.

**Unit Receipt & Processing** (`$15/unit`, key `unit_receipt`)
= Receipt Log rows in the billing period (by `Received Date`) where `Item Type == "Unit"`.

**Small Parts Check In** (`$0.77/part`, key `small_part_checkin`, tiered label on the Breakdown sheet)
= Receipt Log rows in the billing period where `Item Type` is blank/NaN **and** the row auto-qualified (whitelist + valid RMA — see review buckets above). Non-qualifying rows land on the "Unmatched Parts & Units" sheet instead of being billed.

**Unit Picks** (`$8/unit`, key `unit_pick`)
= Shipping History rows in the billing period (by `Pickup Date`), deduped by serial.

**Small Part Picks** (`$8` per unit of Quantity, key `small_part_pick`)
= FedEx Master rows in the billing period, **summed by `Quantity`** (not unique MSO count — this changed in v20; a single MSO with `Quantity=3` now bills as 3 picks, not 1). On the Excel side this is a **live formula**, `=SUM('Part Testing & Programming'!G:G)` (column G = Quantity), not a hardcoded Python value — so the invoice recalculates if that sheet is ever hand-edited.

> **Parts Testing & Configuration has moved to the Workshop invoice as of v20** — see Module 1 above. This invoice still receives the FedEx file (needed for Small Part Picks and the reference sheet below) and still writes the "Part Testing & Programming" detail sheet with real Individual/Total Part Fee values on every row, but no longer sums those fees into its own Breakdown. `analyze_storage()` gets `programming_df`/`part_type_totals` from the shared `storage_builder.analyze_fedex()` helper the same way it always has — only `_build_breakdown()` stopped using `part_type_totals` for pricing.

Part-type classification (`classify_part_type()` / `PART_TYPE_MAP` in `storage_builder.py`) and default pricing (`DEFAULT_PART_TYPE_PRICES`) are documented under Module 1 now, since that's where they're billed:

| Part Type | Keywords in part code | Default Price |
|-----------|----------------------|-------|
| PSU | `PSU` | $7 |
| Mainboard Configure for Dispatch | `MAINBOARD`, `MAINBRD` | $52 |
| Mainboard | (standalone, no dispatch keyword) | $37 |
| AC-PCA | `AC-PCA` | $4 |
| Keypad | `KEYPAD` | $4 |
| Maintouch | `MAINTOUCHPCA`, `MAINTOUCH` | $37 |
| EXT-INPUT | `EXT-INPUT` | $7 |
| OPS-PCA | `OPS-PCA` | $4 |
| USB | `OPS-`, `OPS4-`, `AP-WIFI`, `WIFI`, `BT`, `-NP`, `-7P`, `-5P`, `-CP`, `-C1-` | $4 |
| SPEAKER | `SPEAKER` | $4 |
| CONSOLE | `CONSOLE` | $0 (present in pricing table, no invoice line item wired for it yet) |

Unclassified part codes get `Type = None` — they still appear on the `Part Testing & Programming` sheet, but at $0 and are excluded from `part_type_totals`.

> **Qty=0 rule:** FedEx rows with `Quantity` blank or `0` are rewritten to `1` before any calculation.

### Pricing is config-driven, not hardcoded
`DEFAULT_PART_TYPE_PRICES` and `DEFAULT_LINE_PRICES` in `storage_builder.py` are just fallback defaults. `load_prices()` merges in overrides from `storage_prices.json` (created at runtime, not checked into the repo) if present. The **Config page** in the frontend has a full "Storage Invoice Pricing" admin UI (`/get_storage_prices`, `/set_storage_prices`) to edit and reset both the line-item prices and the part-type testing prices without touching code — this same config now feeds **both** invoices' pricing.

### Breakdown row layout (v20)
Rows 9 Warehouse Storage header, 10-11 Unit/Pallet Storage · 13 Warehouse Ins & Outs header, 14-17 Receipt/Small Parts/Unit Picks/Small Part Picks · totals at **19 (Subtotal, `=SUM(E10:E17)`) / 20 (Tax) / 21 (Total)**. The old "Parts Testing & Configuration" section (rows 19-29) and its totals (31-33) are gone — don't assume row 19 still means "section header" if you're diffing against a pre-v20 export.

### Output Excel sheets
- `Breakdown` — Summary invoice (same layout/structure as Module 1's Breakdown)
- `Unit Storage` — Columns: `ActualModel`, `Actual Serial`, `Storage`
- `Unit Receiving` — Columns: `Actual Model`, `Actual Serial`, `Receipt Fee`
- `Units Shipped` — Columns: `MSO`, `Pickup Date`, `Model`, `Serial`, `Tracking`, `Sales Order Number`, `Out Fee`
- `Part Testing & Programming` — Columns: `MSO`, `Request Date`, `Outbound Tracking`, `Part #`, `Type`, `Serial`, `Quantity`, `Individual Part Fee`, `Total Programming Fee`, `Part Pick Fee`
- `Small Parts Check In` — Columns: `Checkin Date`, `ID`, `Part #`, `Price`
- `Unmatched Parts & Units` — **not in earlier versions of this doc.** All Receipt Log rows that failed whitelist/RMA validation, original columns preserved plus a `_Source` tag. Written even when empty (shows a "No unmatched items" placeholder row).

---

## Module 3: FedEx Shipment Upload (Promethean, new in v23)

**Not an invoice** — this is a bulk "call" import file for USSI's ticketing system (an NGSC-style importer; see the template's own "Instructions - please read" sheet for the system's own field-limit docs). Each row of FedEx's raw monthly billing export becomes one call/ticket, billed to Promethean at a markup over what FedEx actually charged. It shares the Promethean sub-nav with Workshop and Storage but has nothing to do with either invoice's data.

**Client tab / sub-nav:** `promethean` → "FedEx Shipment Upload" (`page-fedex-shipment`)
**Routes:** `POST /analyze_fedex_shipment` (step 1) → `POST /build_fedex_shipment` (step 2) → `GET /stream_fedex_shipment` (SSE)
**Template:** `template/FedEx_Shipment_Upload_Template.xlsx` — a header-only copy of the ticketing system's real `_uploadsheet` import template (also keeps the template's own `Instructions - please read` sheet, untouched)
**Builder:** `fedex_shipment_builder.py`
**Config:** `fedex_shipment_defaults.json` (site info + margin divisor + static call fields — see below)

### Input file
| File | Type | Purpose |
|------|------|---------|
| Raw FedEx Export | `.xlsx`, single sheet | FedEx's own monthly billing export, ~210 columns, one row per tracking ID. Only ~12 columns are used (see `RAW_COLS` in the builder). |

There's no dedup/master-file concept here — every run bills whatever's in the uploaded raw file for that period. If FedEx ever re-sends a corrected file for a month already billed, re-running this would double-bill those tracking IDs; nothing currently detects that (see "Known gaps" below).

### Two-step flow
1. **`/analyze_fedex_shipment`** — uploads the raw file, form fields `period_label` (e.g. `"June"`, goes into the `Summary` column) and `call_date` (single date applied to every row's `CallRcvd`/`Due By Date`), runs `analyze_fedex_shipment()`, and returns counts + two review lists without writing anything: `defaulted_rows` (shipments with no Recipient Company/Name — see "Site info fallback" below) and `skipped_rows` (no tracking ID, no charge, or a duplicate tracking ID within the same raw file). Stashes the built rows in a per-session `_fedex_shipment` (`SessionStore`).
2. **`/build_fedex_shipment`** — reads back the stashed analysis, spawns a background thread that writes the rows into a copy of the template via `build_fedex_shipment_upload()`, streams progress over SSE, returns the output filename + counts in the `done` payload.

### Key data logic
**Pricing** — `Event1Price = floor(Net Charge Amount / margin_divisor, 2 decimals)`. Default `margin_divisor = 0.85` (≈15% markup on FedEx's own net charge), config-driven via `fedex_shipment_defaults.json`. **This is a truncation, not a round** — verified against USSI's real June 2026 upload: floor gives zero mismatches across all 260 rows, naive rounding mismatches ~27% of them by a cent.

**Recipient → site info mapping** (`SiteName`, `MainContact`, `Address`, `Address2`, `City`, `State`, `ZipCode`) — passes through the raw file's `Recipient Company` / `Recipient Name` / `Recipient Address Line 1` / `Recipient Address Line 2` / `Recipient City` / `Recipient State` / `Recipient Zip Code` **as typed, unmodified**, with two exceptions:
- **Site info fallback**: when `Recipient Company` **and** `Recipient Name` are *both* blank (shipments routed back to USSI's own dock rather than out to an end customer), the whole site-info block — not just the blank fields — is replaced with USSI's canonical office info from `fedex_shipment_defaults.json`. These are what `defaulted_rows` reports back in the review step.
- **Canonical-casing normalization**: independently of the fallback above, any individual field that case-insensitively matches USSI's own canonical value gets normalized to that exact casing, even when the rest of the row's recipient info is left as typed (confirmed against two separate real rows in the June sample: one where `MainContact` "Matt Shaw"→"MATT SHAW" while `SiteName` stayed raw "USSI - PROMETHEAN" untouched; another where `SiteName` "United Service Source"→"UNITED SERVICE SOURCE"). Don't try to collapse this into "detect if this is USSI's own address" — it's applied per-field against the canonical strings, not as an address/zip lookup.

**ZIP codes** — truncated to the leading run of digits, capped at 5 (`_clean_zip()`). Handles both zip+4 concatenated with no separator (`"32940754199"` → `"32940"`) and stray trailing junk (`"32940-7541-26"` → `"32940"`). Non-numeric-leading codes (Canadian, etc.) pass through unchanged.

**CustomerPO** — `Original Customer Reference`, falling back to `Original Ref#3/PO Number` if blank (not observed in the June sample, but the two matched in every row where both were populated).

**Everything else** is a static value from `fedex_shipment_defaults.json`, not derived from the raw file per row: `OrgID`, `CustomerID`, `BillCustomerID`, `CallType`, `CallRcvdTime`, `Status`, `QueueID`, `DUE`, `Tech`, `Event1` (the ticketing system's charge code, `PMTH-SHIP`). `CallRcvd`/`Due By Date` are a single date supplied per run (`call_date` form field) — the raw file's own `Invoice Date`/`Shipment Date` columns vary per FedEx sub-invoice within the same month, so they aren't a reliable per-row source.

### `fedex_shipment_defaults.json` (config-driven, editable via Config page endpoints)
```json
{
  "margin_divisor": 0.85,
  "event1_code": "PMTH-SHIP",
  "site_name": "UNITED SERVICE SOURCE",
  "main_contact": "MATT SHAW",
  "address": "7195 WAELTI DR", "address2": "STE 101",
  "city": "MELBOURNE", "state": "FL", "zip_code": "32940",
  "org_id": "Promethean", "customer_id": "Promethean", "bill_customer_id": "Promethean",
  "call_type": "DEPOT", "call_rcvd_time": "17:00:00", "status": "CMP",
  "queue_id": "DIGIMED", "due": "BY", "tech": "E-ERWE"
}
```
Same load/merge pattern as `storage_prices.json`/`amc_prices.json`: `load_defaults()` merges live overrides from this file on top of `DEFAULT_DEFAULTS` in `fedex_shipment_builder.py`. `GET /get_fedex_shipment_defaults` / `POST /set_fedex_shipment_defaults` read/write it — **not yet wired into the Config page UI** (`page-config` in `index.html`), just the backend endpoints; follow the "Storage Invoice Pricing" admin section pattern there if/when that's wanted.

### Output
A single sheet (`_uploadsheet`) matching the ticketing system's own import format — columns are written by header-name lookup (`col_of` dict built from the template's own header row), not hardcoded positions, so the module tolerates the ticketing system reordering its template columns later. `CallRcvd`/`Due By Date`/`CallRcvdTime` are written as real Excel date/time objects (not text), matching what the ticketing system's own real exports use.

### Known gaps
- **No dedup against a previously-billed master** — same limitation as TCL (Module below). If FedEx re-sends a corrected file for an already-billed month, nothing stops a re-run from double-billing those tracking IDs.
- **`fedex_shipment_defaults.json` isn't in the Config page UI yet** — only the API endpoints exist; someone has to either edit the JSON file directly or a small admin form needs adding.
- **Validated only against June 2026** — the mapping (especially the canonical-casing normalization and the site-info fallback) was reverse-engineered from one month's real data (260/260 rows matched exactly). If a future month's raw export has a shipment pattern not seen in June — e.g. a *third* variant of "is this USSI's own address" — re-verify against that month's actual upload before trusting the output blind.

---

## TCL Warehouse Invoice (new — not a real module elsewhere in this doc yet)

**Nav:** own top-level tab (`nav-tcl` / `page-tcl`), not a Promethean sub-page.  
**Route:** `POST /analyze_tcl` → `POST /confirm_tcl` → `GET /stream_tcl`  
**Template:** `template/TCL_Warehouse_Invoice_Template.xlsx` (sheets: `Invoice`, `Line Items`)  
**Builder:** `tcl_builder.py`  
**Bill To / Ship To default:** TTE Technology Inc, 189 Technology Dr., Irvine, CA 92618

### Input Files
| File | Type | Columns |
|------|------|---------|
| Inventory Export | `.xlsx` (sheet 1) | `Transfer Detail ID`, `Model`, `Serial Number`, `Grade`, `Rack`, `Bin`, `Warehouse`, `Received Date` |

The only source file. It has **no pallet or box grouping data** — the warehouse
doesn't track that digitally — so this is an analyze → resolve → build flow,
not a straight compute.

### Unit vs. Part classification
No `Item Type` column (unlike the Storage module). Classified by warehouse
location instead:
- `Rack == "MAIN"` or `Bin == "RCV"` → a whole unit (TV) → **Pallet Storage**
- everything else → a non-serialized part → **Box In Fee**

### Data logic
**Pallet Storage** — ALL units currently in inventory bill every period
(ongoing storage rent, not one-time), regardless of when received. Grouped by
`Received Date` batch. Since pallet assignment isn't in the source file, the
user is prompted per batch for a comma-separated pallet breakdown (e.g.
`7,7,5,7,4` for 26 units) that must sum to the batch quantity. Total pallets
across all batches sets the rate tier: **≤10 pallets → $75/pallet, ≥11 → $60/pallet**
(flat by tier, not marginal).

**Box In Fee** — Only parts *received within the billing period* get a fee this
invoice (one-time in-fee, not recurring). Grouped by `Model` + `Received Date`.
User is prompted per group for a comma-separated box breakdown (e.g. `20,20,10`
for 50 units) that must sum to the group quantity. Each box prices by its own
size:

| Parts in box | Rate |
|---|---|
| 1 (single/serialized) | $3.85 |
| 2-5 | $3.85 |
| 6-10 | $7.70 |
| 11-15 | $11.55 |
| 16-20 | $15.00 |

Box pricing is **per box, flat**, not per part — confirmed against the
client's own prior invoice, where `Qty` on the `Invoice` sheet is a count of
boxes, not parts.

`validate_breakdowns()` in `tcl_builder.py` rejects any breakdown that doesn't
sum to the group's quantity, or any box size outside {1} ∪ [2,20].

### Output Excel sheets
- `Invoice` — Bill To/Ship To, P.O./Terms/Due Date, one summary row per pricing
  tier actually used this period (zero-qty tiers skipped), Subtotal/Tax(7%)/Total.
  Row positions shift dynamically — merges are fully rebuilt each run since the
  number of tier lines varies invoice to invoice.
- `Line Items` — one row per physical TV unit (pallet-grouped; charge placed
  only on each pallet's last row, matching the client's own template
  convention) plus one row per part box (`Serial` = "N/A").

### Known gaps
- No dedup against a "previously invoiced" master — every run bills whatever's
  in the uploaded snapshot for the period. If this becomes recurring, add a
  master-file dedup step like Module 1's, especially for Pallet Storage (a
  unit that ships out should stop billing, but nothing currently detects that
  since there's no shipping-history input for this module).
- The original TCL sample invoice's tax line read "Sales Tax (0.00)" as static
  label text while the formula actually charged 7% (looked like a stale
  placeholder). This module writes the label dynamically from the real rate
  used instead (`Sales Tax (7.00%)`) — worth confirming 7% is correct for this
  account.

---

## Serial Rules Config (`serial_rules.json`)

Editable live from the **Config** page (`page-config`) without redeploying. Each rule:
```json
{
  "prefix": "65W",
  "year_pos": 6,
  "model_base": "AP7-U65",
  "size": "65",
  "o2_rule": "year_pos6"
}
```
- `prefix` — matched against the uppercased serial, **longest prefix wins** when multiple rules match.
- `model_base` — gets `-NA-R` or `-02-NA-R` appended automatically.
- `o2_rule` — `"always"`, `"never"`, or `"year_posN"` (reads the year character at that index of the serial to decide).
- Rules only apply to `sanitizer._serial_to_model_and_size()`; if no rule matches, a large hardcoded fallback table in `sanitizer.py` covers legacy prefixes (AP6/AP9/AP10/APLE/APLX/VTP families), followed by two special-cased overrides (`9A75...V...` and `9B75GP`) that apply on top of either path.
- `GET /config/serial_rules` / `POST /config/serial_rules` read/write this file directly.

## Storage Whitelist (`whitelist_default.json`)
Built-in fallback when no whitelist `.xlsx` is uploaded to `/analyze_storage`. Shape:
```json
{ "parts": ["6141-0100GA-68DD1201", "..."], "unit_models": ["..."] }
```
Matched case-insensitively, stripped of whitespace.

---

## Frontend Patterns (`templates/index.html`)

### Three-level navigation (portal / client / module — see "Portal shell" above for the full picture)
- **Portal-level tabs** (`showPortalPage(name)`): `invoice-generator`, `sms-nonconforming`, `tbd2`. Only `invoice-generator` has real content; the other two are placeholder cards. Training Tracker is a sibling but NOT part of this set — see "Portal shell" above.
- **Top-level client tabs**, inside `invoice-generator` (`showPage(name)`): `promethean`, `amc`, `tcl`, `philips`, `config`. All five are real now — there's no empty placeholder left (this doc said otherwise for a long time; the correction note near the top predates even that).
- **Sub-nav within a client tab** (`showSubPage(name, client)`): currently only `promethean` has one, toggling between `invoice`, `storage`, and `fedex-shipment`.
- `showPortalPage()` toggles `hidden` on `<div id="portal-content-<n>">` (main) and `<div id="portal-body-<n>">` (sidebar, `invoice-generator` only) and `active` on `<button id="portal-nav-<n>">`. `showPage()` toggles `hidden` on `<div id="page-<n>">` and `active` on `<button id="nav-<n>">`. `showSubPage()` does the equivalent for `<div id="page-<sub>">` / `<button id="subnav-<sub>">`.

**To add a new module for an existing client:** add a subnav button + `page-<module>` div inside that client's section, following the `page-invoice`/`page-storage` pattern.
**To add a new client inside Invoice Generator:** add a brand-new top-level page (`nav-<client>`/`page-<client>`, registered in `showPage()`'s `allPages` array) inside `portal-content-invoice-generator` — there's no empty placeholder to reuse anymore.
**To build out one of the placeholder portal tabs (SMS NonConforming, TBD 2):** that's a sibling of Invoice Generator, not a client inside it — see "Portal shell" above.

### File drop-zones
Each upload input has a matching drop-zone div. Pattern:
```html
<label class="drop-zone ..." id="zone-X" for="file-X">
  <div id="icon-X">🗂</div>
  <p id="label-X">Description</p>
  <input type="file" id="file-X" name="field_name" accept=".xlsx" class="hidden" />
</label>
```
Wire in JS: listen for `change` on input → update label text + add `filled` class to zone.

### SSE log streaming
POST to the step-2 route → on success open `EventSource('/stream[_module]')`.
Message types: `ping` (ignore), `log` (append to log box), `done` (show result or error, contains whatever the builder's return payload includes).

### Config page (`page-config`)
Two admin sections, both already built:
1. **Serial Number Configuration** — editable table backed by `serial_rules.json` (add/edit/delete prefix rules, save via `/config/serial_rules`).
2. **Storage Invoice Pricing** — editable line-item and part-type price forms backed by `storage_prices.json` (save/reset via `/get_storage_prices`, `/set_storage_prices`, `savePricing()`/`resetPricing()` in JS).

### Theme
Dark/light toggle via `data-theme` on `<html>`. CSS vars: `--bg`, `--bg-50`, `--bg-100`, `--bg-input`, `--border`, `--text`, `--text-sub`, `--text-muted`. Colors: amber `#f59e0b`, ok green `#22c55e`, warn red `#ef4444`.

---

## Adding a New Client / Module — Step-by-Step

1. **Get the sample invoice (or, for a non-invoice module like FedEx Shipment Upload, a real before/after example)** for the new client/module — study the sheet names, column layout, line items, and pricing (or the raw→output mapping).
2. **Document the source files** — for each input file, note: file type, column names, how to filter by date, and which fields drive which line items. If you have both a raw input and a real finished output for the same period, reverse-engineer the mapping field-by-field and verify it row-for-row against the real output before writing any code — that's how Module 3's pricing formula (floor, not round) and its casing-normalization edge case were caught; both would've been wrong if assumed from a first glance at a few rows.
3. **Decide: single-step or two-step build?** Don't default to two-step just because the existing modules use it. Workshop and Storage need it because their source data has real ambiguity (unresolved serials, unmatched parts) that needs a human decision before billing. A module whose transform is fully deterministic — like Module 3 — can still use two steps, but for a lighter reason: giving the user a chance to see counts/warnings (e.g. "4 rows defaulted to fallback site info") before anything's written, not because there's a correction UI. Know which reason applies before copying the review-table pattern wholesale.
4. **Copy the storage_builder.py (or fedex_shipment_builder.py, for something that isn't an invoice) pattern** — create `<module>_builder.py` with an analyze/build split (or a single `build_<module>()` if no review step is needed). Takes file paths + params, returns a dict of counts/totals.
5. **Add routes to `app.py`** — mirror `/analyze_storage` + `/confirm_storage` + `/stream_storage` (or `/analyze_fedex_shipment` + `/build_fedex_shipment` + `/stream_fedex_shipment` for a deterministic two-step module, or `/generate_<module>` + `/stream_<module>` for a true single-step module). Copy the relevant block exactly, swap the builder import and field names. Declare a module-level `_<module> = SessionStore()` instance for per-session state — don't use a bare dict.
6. **Wire into the frontend.** All five client tabs (`promethean`, `amc`, `tcl`, `philips`) are real now — there's no empty placeholder left. A **new client** needs a brand-new top-level page (`nav-<client>`/`page-<client>`, registered in `showPage()`'s `allPages` array). A **new module for an existing client** (like Module 3) follows the `promethean` sub-nav pattern instead — add a `side-sublink` button with a `subnav-<sub>` id + `page-<sub>` div, and register `<sub>` in `showSubPage()`'s `subMap` in `app.js`.
7. **Place the template** in `template/` folder. If the module writes into a format owned by an external system (like Module 3's ticketing-system import), keep the template as close to that system's own real template as possible — write by header-name lookup, not hardcoded column positions, so it survives that system reordering its own template later.
8. If the new module needs its own pricing/config to be admin-editable, follow the `storage_prices.json` / Config-page pattern rather than hardcoding. Note that as of v23, `fedex_shipment_defaults.json` has the load/save backend endpoints but isn't hooked into the Config page UI yet — that's still a loose end worth closing if this pattern gets reused again.

**What to tell Claude in the new session:**
> "Read PROJECT_BRIEF.md first. I need to add a new module for [Client/purpose]. The source files have these columns: [paste headers]. Here's a sample of the real finished output for one period, and the raw input that produced it, if you have both. Here are the pricing/transform rules: [list]. Does this need a review/confirm step, or can it go straight from upload to build?"

You should only need to attach: **the zip** + **the sample invoice Excel** (or, for a non-invoice module, a real raw-input/finished-output pair). No other data files needed.

---

## Common Gotchas

- **The portal must run as one Gunicorn worker.** Every invoice module's analyze→confirm state and SSE progress queue lives in its `SessionStore`, which is process memory and cannot be read by a second worker. With `--workers 2`, Analyze can succeed in worker A and Confirm can immediately fail with `No analysis loaded` in worker B; the progress stream can be separated from its build queue the same way. Both `Procfile` and `render.yaml` therefore use `--worker-class gthread --workers 1 --threads 8`: one shared process for state, with eight threads for concurrent HTTP/SSE requests. Do not raise the worker count without first moving all `SessionStore` state and queues to a shared external store.
- **Philips Month End Report `Inventory.Size` is optional.** `philips_builder.analyze_philips()` treats that column as an optional precomputed box-footprint value. If the column is absent, blank, or non-numeric, it derives square footage from `Model` through the persisted Philips Dimensions reference. Only models that still cannot be matched appear in the review screen for a manual sq-ft value; those values are saved for future invoices.
- **Generated-output authorization is persisted separately from `SessionStore`.** `_register_output()` keeps its in-memory cache, but also writes an atomic hashed marker under `outputs/.access/`; `_registered_output_subsection()` falls back to that marker. This keeps completed files downloadable after a cold cache and preserves safe subsection-level authorization. Do not replace this with a process-local-only mapping or authorize downloads from filename patterns.
- **Portal-mounted Training Tracker isn't in `app.py`'s own `url_map`.** Since it's composed at the WSGI level (`DispatcherMiddleware`), `app.app.url_map.iter_rules()` won't show any `/training-tracker/*` routes — that's expected, not a sign the mount failed. Test it by actually hitting `/training-tracker/...` over HTTP, not by inspecting the main Flask app's routing table.
- **Don't call `training_tracker.app.init_db()` inside `if __name__ == "__main__":` in `training_tracker/app.py`.** That guard never runs when the module is imported (which is what happens every time — it's mounted, not executed directly), so DB init has to stay where it is now: called once at import time in the portal's `app.py`, right after the import.
- **Training Tracker's `init_db()` seeds the default admin user inside a `BEGIN IMMEDIATE` transaction, not a bare `SELECT COUNT(*)` check-then-insert.** This protects initialization if the app is ever run with multiple processes: a check-then-insert has a race window where two workers can both see zero users and both try to insert `"admin"`, crashing the second worker on the `users.username` UNIQUE constraint (this actually happened during earlier Render deploys). `BEGIN IMMEDIATE` takes SQLite's write lock immediately rather than lazily, so the second worker blocks until the first commits and then correctly sees the row already exists. If you ever touch `init_db()` again, keep it inside that transaction rather than reverting to a plain check-then-insert.

- **openpyxl theme colors**: Use `Color(theme=N, tint=T, type="theme")` — don't use hex colors for fills that need to match the template exactly.
- **Formula recalculation**: openpyxl writes formulas as strings. Values won't show until opened in Excel. This is expected and correct behavior.
- **CSV quoting**: All CSVs from this system use `="value"` Excel-formula quoting on **both headers and values**. Always clean with `clean_csv_df()` (Storage) or the inline `clean()` closure (Workshop's shipping CSV in `apply_dedup()`).
- **FedEx Quantity=0**: Always rewrite blank/0 → 1 before calculations. Centralized in `storage_builder.analyze_fedex()` (v20) — both invoices inherit this from the one place.
- **`TblPartTesting` table name is load-bearing**: the Workshop Breakdown's SUMIFS formulas (rows 34-43) reference this Excel Table by name. `storage_builder._build_part_testing()` self-clears any stale table registration on that sheet before re-adding it (`ws._tables.clear()`) — needed because the template ships with its own pre-existing table of the same name, and openpyxl errors on duplicate table names within a sheet. If you ever rename the table, update both the sheet-writer and every SUMIFS formula string in `builder.py`'s `PT_LINES`.
- **Don't globally clear `_tables` in `builder.build()`**: unlike `storage_builder.build_storage_invoice()` (which does `for ws in wb.worksheets: ws._tables.clear()` up front and rebuilds every table from scratch), `builder.py` only expands the *existing* Depot Repair / Triage Units tables' `.ref` ranges rather than recreating them — a blanket clear there would silently strip their table styling.
- **Date formats in CSVs**: Shipping History and Receipt Log use `MM-DD-YYYY`. Use `format="%m-%d-%Y"` in `pd.to_datetime()`. FedEx Master's `Request Date` and the Workshop master's `Month`/`Date` columns are parsed without an explicit format (`errors="coerce"`).
- **"Shipped" for Storage purposes = `Pickup Date` filled in**, not the `Shipped Date` column. Don't conflate the two when extending this module.
- **Units vs Parts in Inventory**: `Item Type` column — `"Unit"` = panel unit, blank/other = part.
- **Serial number dedup**: Always `drop_duplicates(subset=["Serial Number"])` before counting units. The same serial can appear multiple times.
- **Tab name in template**: `delete_rows(1, ws.max_row)` before writing — the template sheets have sample data that must be cleared first. Both builders also clear existing `_tables` / conditional-formatting rules before writing so the output doesn't inherit stale template artifacts.
- **Server-side session state is per-browser-session, not a shared global dict** — every module's `_<module> = SessionStore()` instance keys its in-progress analysis + SSE queue by a signed-cookie session id (`_sid()`), backed by `.flask_secret` so it survives a relaunch. (This doc previously said the opposite for several versions — see the v23 correction note at the top. Server restarts between step 1 and step 2 still lose in-flight analysis, since it's in-memory only; that part's still true.)
- **RMA validation for Small Parts** requires an *exact* `M` + 8-digit pattern (`^M\d{8}$`) — anything with a suffix, prefix, or wrong digit count falls through to the Unmatched sheet rather than being silently billed.
- **Two different things are both called "FedEx" in this app — don't conflate them.** The **FedEx Master Sheet** (Module 1/Module 2's `Outbound Tracking`/`MSO`/`Quantity` file) drives Parts Testing & Configuration and Small Part Picks on the *invoices*. The **FedEx Shipment Upload** (Module 3, v23) is a completely separate raw billing export with completely different columns (`Net Charge Amount`, `Recipient Company`, tracking IDs) feeding a *non-invoice* ticketing-system import. Same shipping carrier, unrelated files, unrelated code paths (`storage_builder.analyze_fedex()` vs `fedex_shipment_builder.analyze_fedex_shipment()`) — don't assume one can substitute for the other.
- **Floor, not round, when a billing formula divides by a margin**: Module 3's `Event1Price = floor(net / 0.85, 2 decimals)` was originally implemented with `round()` and matched most rows by coincidence — only diffing against the *entire* real output caught that ~27% of rows were off by a cent. Any time you're reverse-engineering a pricing formula from a real example file, verify every row's calculated value against the real file's value before trusting a formula that "looks right" on a handful of spot checks.
