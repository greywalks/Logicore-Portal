# Logicore Portal

A small internal web portal for USSI billing/logistics tooling. It's built as
a **Flask + Tailwind** single-page app with a two-layer sidebar: a top-level
**Portal** tab picker, and — inside the **Invoice Generator** tab — the
full multi-client invoice suite (Promethean, AMC, TCL, Philips).

```
Portal
├── Invoice Generator   ← this is the whole app that used to be the repo root
│   ├── Promethean (Workshop / Storage / FedEx Shipment Upload)
│   ├── AMC
│   ├── TCL
│   ├── Philips
│   └── Config
├── SMS NonConforming    (placeholder — not built yet)
├── TBD 1                (placeholder)
└── TBD 2                (placeholder)
```

For anything about the invoice-generation logic itself — pricing rules,
input file formats, how a module's two-step build works, config-editable
pricing — see **[`PROJECT_BRIEF.md`](./PROJECT_BRIEF.md)**. That file is
written for a coding assistant picking the project back up cold, but it's
also the most complete engineering reference for a human. `README.txt`
has the original end-user, non-technical usage walkthrough for the
Promethean and TCL modules.

---

## Running locally

**Windows, no terminal needed:** double-click `Launch_Invoice_Generator.bat`.
It installs dependencies on first run and opens your browser to the app.

**Any OS, from a terminal:**
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
Opens at `http://127.0.0.1:5000`.

---

## Deploying to Render

This repo is set up to deploy as-is:

1. Push it to a GitHub repo.
2. In the [Render dashboard](https://dashboard.render.com), choose
   **New → Blueprint** and point it at the repo. `render.yaml` at the repo
   root defines the service — build command, start command, and health
   check are all pre-configured, so there's little left to fill in.
   (Alternatively: **New → Web Service**, build command
   `pip install -r requirements.txt`, start command from `Procfile`.)
3. Render's free web services have **no persistent disk** — the filesystem
   resets on every deploy/restart. That's fine for how this app is used
   (each invoice run is self-contained: upload → generate → download), but
   two things to know:
   - Set a `FLASK_SECRET_KEY` environment variable (the Blueprint
     auto-generates one) so signed-in sessions survive a restart instead of
     silently rotating.
   - Anything an admin edits from the in-app **Config** page (serial
     rules, storage pricing, AMC/Philips dimension tables) resets to its
     committed default on the next deploy. If that becomes a problem,
     attach a Render Disk and point those config paths at it — see the
     "Deploying to Render" section of `PROJECT_BRIEF.md` for specifics.
4. The service exposes `/healthz` for Render's health check and `/version`
   for a quick sanity check of what's deployed.

---

## Rebuilding the CSS

Tailwind is compiled ahead of time into `static/css/tailwind.css` — the app
never loads Tailwind from a CDN, so styling has no runtime network
dependency. If you add new Tailwind utility classes to `templates/index.html`
or `static/js/app.js`, rebuild:

```bash
cd build
npm install
npm run build
```

`build/` is dev tooling only — not needed to run the app, and its
`node_modules/` is gitignored.

---

## Project layout

See the "Architecture Overview" section of `PROJECT_BRIEF.md` for the full
annotated file tree and the "Portal shell" section for how the three levels
of navigation (Portal tabs → client tabs → module sub-nav) fit together.

## Adding a new module or client

Also documented in `PROJECT_BRIEF.md` — see "Adding a New Client / Module —
Step-by-Step" and the "Portal shell" section for whether what you're adding
is a new *client inside Invoice Generator* or a new *sibling portal tab*
(like SMS NonConforming). They're different things and use different
patterns.
