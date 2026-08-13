# Promethean Invoice Generator — Setup & Usage

## What This Does
Reads three source files, applies billing rules, and outputs a formatted
Promethean Workshop invoice (.xlsx) — ready to send.

Runs as a local web app: double-click to launch, use in your browser.

---

## First-Time Setup (One Time Only)

1. **Install Python**
   - Go to https://www.python.org/downloads/
   - Download the latest version (3.11 or higher)
   - Run the installer — CHECK THE BOX: "Add Python to PATH"
   - Click Install Now

2. **Place the template file**
   - Copy `Sample_Promethean_Workshop_Invoice.xlsx` into the `template` folder

3. **Double-click `Launch Invoice Generator.bat`**
   - First run installs packages (takes ~30 seconds, one time only)
   - Your browser opens automatically to the app

---

## Every Time You Use It

1. Double-click **`Launch Invoice Generator.bat`**
2. Your browser opens to the Invoice Generator
3. Upload your three source files (click each zone or drag-and-drop):
   - **Repair Data** — current period repairs and triages (.xlsx)
   - **Previously Invoiced Master** — full billing history (.xlsx)
   - **Shipping History** — all outbound shipments (.csv)
4. Set the billing period date range
5. Confirm Call ID, Customer, Invoice Date, Completed Date
6. Click **Generate Invoice**
7. Watch the live log — then click **Download Invoice** when ready

---

## Billing Rules Applied Automatically

| Rule | What It Does |
|------|-------------|
| Duplicate prevention | If previously invoiced AND not reshipped since → excluded |
| Previously triaged discount | Repaired unit that was previously triaged → lower repair-only rate |

---

## Pricing Reference

| Category | Size | Rate |
|----------|------|------|
| Triage & Repair — Basic | Small (65"–75") | $110 |
| Triage & Repair — Basic | Large (86") | $135 |
| Triage & Repair — Heavy | Small (65"–75") | $220 |
| Triage & Repair — Heavy | Large (86") | $268 |
| Triage Only — Basic | Small | $86 |
| Triage Only — Basic | Large | $101 |
| Triage Only — Heavy | Small | $152 |
| Triage Only — Heavy | Large | $181 |
| Repair Only (prev triaged) — Basic | Small | $64 |
| Repair Only (prev triaged) — Basic | Large | $74 |
| Repair Only (prev triaged) — Heavy | Small | $108 |
| Repair Only (prev triaged) — Heavy | Large | $127 |
| Salvage of Hardware and Scrap | Any | $28 |
| Reboxing Fee | — | $50 |
| Special Warehouse Projects | — | $75/hr |

Tax rate: **7%**

---

## TCL Warehouse Invoice (new)

A separate top-level "TCL" tab, for TTE Technology Inc's warehouse account.
(Note: this README section only covers TCL — the Storage and Philips modules
that also now live in this app aren't documented here yet.)

1. Click the **TCL** tab
2. Upload the raw **Inventory Export** (.xlsx — one sheet, straight from the
   warehouse system, no pre-processing needed)
3. Set the billing period (parts *received* in this range get billed this
   invoice; units already in inventory bill for storage regardless of when
   they arrived) and invoice details, then click **Analyze Inventory**
4. The next screen asks you to assign **pallets** and **boxes** — the raw file
   doesn't track either, so you'll see one field per batch of units (grouped
   by the date they arrived) and one field per group of parts (grouped by
   part number + date received). Enter a comma-separated list of counts that
   adds up to the batch/group total, e.g. `7,7,5,7,4` for 26 units, or
   `20,20,10` for 50 parts. A green checkmark means it balances.
5. Click **Generate Invoice**, then **Download** when ready

Pricing applied automatically:
- Pallet Storage: $75/pallet (≤10 total pallets this invoice) or $60/pallet
  (≥11 total pallets)
- Non-serialized parts, per box: $3.85 (2-5 parts), $7.70 (6-10), $11.55
  (11-15), $15.00 (16-20). A box of exactly 1 part bills at $3.85.

Lines with zero quantity are automatically left off the invoice.

---

## Folder Structure

```
invoice_app/
├── app.py                          ← Flask server (do not move)
├── requirements.txt                ← Auto-installed dependencies
├── Launch Invoice Generator.bat    ← Double-click to run
├── README.txt                      ← This file
├── template/
│   ├── Sample_Promethean_Workshop_Invoice.xlsx
│   ├── Sample_Promethean_Storage_Small_Parts_Invoice.xlsx
│   └── TCL_Warehouse_Invoice_Template.xlsx
├── templates/
│   └── index.html                  ← Web UI
├── uploads/                        ← Temp upload storage (auto-created)
└── outputs/                        ← Generated invoices saved here
```

---

## Troubleshooting

**Browser doesn't open automatically**
→ Open your browser and go to: http://127.0.0.1:5000

**"Python is not installed" error**
→ Install from python.org; check "Add Python to PATH" during install.

**"Template not found" error**
→ Place `Sample_Promethean_Workshop_Invoice.xlsx` inside the `template` folder.

**Port already in use**
→ Another program is using port 5000. Close other apps or restart your computer.

**Numbers seem off**
→ Verify the Previously Invoiced Master has sheets named exactly:
  "Repair Log" and "Triage Log"

**To stop the server**
→ Click the terminal window and press Ctrl+C
