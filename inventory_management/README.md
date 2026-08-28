# Inventory Management

Mounted Flask sub-app for reconstructing Promethean unit/MSO lifecycle history from operational exports.

## Route

`/inventory-management/`

The production entry point is `wsgi:application`, which composes this app alongside the existing portal and Training Tracker.

## Persistent storage

Set `INVENTORY_DATA_DIR` to a persistent disk path in production. On Render, a typical configuration is:

```text
INVENTORY_DATA_DIR=/var/data/inventory-management
```

The module creates:

- `inventory.db` — SQLite lifecycle database
- `uploads/` — retained copies of uploaded source files

These runtime artifacts are ignored by Git and must not be committed.

## Supported imports

The importer identifies source type by column headers rather than filename:

- Receiving Export CSV/XLSX
- Shipping Export CSV/XLSX
- Inventory Export CSV/XLSX
- Promethean production workbook (`Repair Data` sheet)
- USSI Promethean FedEx master workbook

Excel-exported values such as `="VALUE"` are normalized automatically.

## Data model

The database is event-based rather than one-row-per-serial. A serial may be received, repaired, shipped, returned, and associated with multiple MSOs repeatedly without overwriting prior history.

Core tables:

- `import_batches` — file hashes, timestamps, audit counts
- `assets` — normalized Serial Number identities
- `cases` — normalized MSO identities
- `events` — immutable lifecycle facts with source-specific JSON details

Files are deduplicated by SHA-256. Individual lifecycle facts are also fingerprinted so unchanged rows repeated in a newer snapshot do not duplicate existing history.

## Search

- Serial Number → complete cross-source lifecycle timeline
- MSO → directly associated events and serials
- Model Number → all matching lifecycle events with CSV export

Inventory exports are treated as snapshots. Their `Received Date` is retained as source metadata; the inventory observation itself is timestamped when the snapshot is imported so the UI does not falsely claim that the snapshot proves the exact time a unit entered its current rack/bin.

## Permissions

For compatibility with the existing portal without rewriting the large portal shell, Inventory Management currently reuses the legacy `tbd2` permission key. `wsgi.py` relabels that permission to **Inventory Management** at runtime, so existing grants continue to work while TBD 2 disappears from the user-facing navigation.
