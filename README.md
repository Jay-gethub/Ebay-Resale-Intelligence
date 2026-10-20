# eBay Resale Intelligence

A local-first project for recording marketplace listing snapshots and analyzing
resale opportunities. The listing-history feature stores observations in SQLite;
it does not call eBay APIs.

## Setup

Use Python 3.13 (or a compatible Python version) and create a virtual
environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run the tests

```powershell
python -m pytest
```

The tests use temporary SQLite databases and synthetic listings. They do not
access eBay or require credentials.

## Listing history data model

Each observation is a separate row containing an eBay item ID, title, listing
URL, exact observed price, validated ISO 4217 currency code, optional
condition, UTC observation timestamp, and source. Currency validation uses
`pycountry`'s bundled ISO code data, so it does not require a network request.
Multiple observations of an item are preserved, including when its price
changes.

By default, the database is stored at `data/listing_history.sqlite3`. That file
and SQLite sidecar files are excluded by `.gitignore`. Applications can instead
pass a different database path to the functions in `src/listing_history.py`.
Saving a snapshot initializes the database automatically. To retrieve history
before any snapshots have been saved, call `initialize_database()` first:

Example:

```python
from decimal import Decimal

from src.listing_history import (
    ListingSnapshot,
    get_item_price_history,
    initialize_database,
    save_listing_snapshot,
)

snapshot = ListingSnapshot(
    item_id="synthetic-item-001",
    title="Example listing",
    listing_url="https://example.invalid/item/001",
    price=Decimal("24.99"),
    currency="USD",
    condition="Used",
    source="manual",
)
initialize_database()
save_listing_snapshot(snapshot)
history = get_item_price_history("synthetic-item-001")
```
