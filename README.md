# Logicore Portal

A small internal web portal for USSI billing/logistics/training tooling. It's
built as a **Flask + Tailwind** single-page shell with a top-level sidebar
tab picker:

```
Portal
├── Invoice Generator   ← the multi-client invoice suite (Promethean, AMC, TCL, Philips)
├── SMS NonConforming    (placeholder — not built yet)
├── Training Tracker     ← real app: weekly curriculum, attendance, sign-offs
└── TBD 2                (placeholder)
```

**Invoice Generator** is a single-page app (hidden-div navigation, no page
reloads) — see [`PROJECT_BRIEF.md`](./PROJECT_BRIEF.md) for the full
engineering reference: pricing rules, input file formats, the two-step
build pattern each module follows, config-editable pricing, etc.
`README.txt` has the original non-technical, step-by-step usage walkthrough
for the Promethean and TCL modules specifically.

**Training Tracker** is a real, separate multi-page Flask app (its own
login, roles, and SQLite database) mounted at `/training-tracker/` — click
its sidebar entry to open it as its own set of pages, styled to match the
portal but otherwise independent. See the "Training Tracker" section of
`PROJECT_BRIEF.md` for how the mount works and what was changed to make it
fit the portal's look.

---

## Running locally

**Windows, no terminal needed:** double-click `Launch_Invoice_Generator.bat`.
It installs dependencies on first run and opens your browser to the portal.

**Any OS, from a terminal:**
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
Opens at `http://127.0.0.1:5000`. Training Tracker's default login is
`admin` / `admin` — change it immediately from **Account** once you're in
(there's a banner reminding you until you do).

---

## Rebuilding the CSS

Tailwind is compiled ahead of time into `static/css/tailwind.css` for the
Invoice Generator tab — it never loads Tailwind from a CDN, so that tab's
styling has no runtime network dependency. If you add new Tailwind utility
classes to `templates/index.html` or `static/js/app.js`, rebuild:

```bash
cd build
npm install
npm run build
```

`build/` is dev tooling only — not needed to run the app, and its
`node_modules/` is gitignored.

Training Tracker (`training_tracker/templates/*.html`) is the one
exception: it stays on the Tailwind CDN script deliberately, because its
Appearance admin panel lets an admin change theme colors at runtime from
the database — a precompiled stylesheet can't do that. See the "Training
Tracker" section of `PROJECT_BRIEF.md` for why.

---

## Project layout

See the "Architecture Overview" section of `PROJECT_BRIEF.md` for the full
annotated file tree, the "Portal shell" section for how the three levels of
navigation (Portal tabs → client tabs → module sub-nav) fit together, and
the "Training Tracker" section for how that mounted sub-app works.

## Adding a new module, client, or portal tab

Also documented in `PROJECT_BRIEF.md` — see "Adding a New Client / Module —
Step-by-Step" and the "Portal shell" section for whether what you're adding
is a new *client inside Invoice Generator*, a new *sibling portal tab* (like
SMS NonConforming), or something with real multi-page/auth needs of its own
(like Training Tracker). They're different things and use different
patterns.
