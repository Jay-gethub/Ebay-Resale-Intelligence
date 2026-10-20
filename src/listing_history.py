"""SQLite persistence for local listing observations."""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Generator

import pycountry

DEFAULT_DATABASE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "listing_history.sqlite3"
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS listing_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL CHECK (length(item_id) > 0),
    title TEXT NOT NULL CHECK (length(title) > 0),
    listing_url TEXT NOT NULL CHECK (length(listing_url) > 0),
    price TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (length(currency) = 3),
    condition TEXT,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK (length(source) > 0)
)
"""

_SNAPSHOT_COLUMNS = """
id, item_id, title, listing_url, price, currency, condition, observed_at, source
"""


@dataclass(frozen=True)
class ListingSnapshot:
    """One observation of a listing at a particular point in time."""

    item_id: str
    title: str
    listing_url: str
    price: Decimal
    currency: str
    condition: str | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "manual"

    def __post_init__(self) -> None:
        for name in ("item_id", "title", "listing_url", "source"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")

        try:
            normalized_price = Decimal(str(self.price))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("price must be a valid decimal amount") from error
        if not normalized_price.is_finite() or normalized_price < 0:
            raise ValueError("price must be finite and non-negative")
        object.__setattr__(self, "price", normalized_price)

        normalized_currency = self.currency.upper()
        if pycountry.currencies.get(alpha_3=normalized_currency) is None:
            raise ValueError(f"unknown ISO 4217 currency code: {self.currency}")
        object.__setattr__(self, "currency", normalized_currency)

        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        object.__setattr__(
            self, "observed_at", self.observed_at.astimezone(timezone.utc)
        )


@contextmanager
def _database_connection(
    db_path: str | Path,
) -> Generator[sqlite3.Connection, None, None]:
    """Open a connection, transact within its scope, and always close it."""
    with closing(sqlite3.connect(db_path)) as connection:
        with connection:
            yield connection


def initialize_database(
    db_path: str | Path = DEFAULT_DATABASE_PATH,
) -> None:
    """Create the database directory, listing snapshot table, and lookup index."""
    database_path = Path(db_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with _database_connection(database_path) as connection:
        connection.execute(_CREATE_TABLE)
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_listing_snapshots_item_observed
            ON listing_snapshots (item_id, observed_at, id)
            """
        )


def save_listing_snapshot(
    snapshot: ListingSnapshot,
    db_path: str | Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Persist a new observation and return its database ID."""
    initialize_database(db_path)
    with _database_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO listing_snapshots (
                item_id, title, listing_url, price, currency,
                condition, observed_at, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.item_id,
                snapshot.title,
                snapshot.listing_url,
                str(snapshot.price),
                snapshot.currency,
                snapshot.condition,
                snapshot.observed_at.isoformat(),
                snapshot.source,
            ),
        )
        return int(cursor.lastrowid)


def get_item_price_history(
    item_id: str,
    db_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[ListingSnapshot]:
    """Return all saved observations for an item, oldest first."""
    with _database_connection(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            SELECT {_SNAPSHOT_COLUMNS}
            FROM listing_snapshots
            WHERE item_id = ?
            ORDER BY observed_at ASC, id ASC
            """,
            (item_id,),
        ).fetchall()
    return [_snapshot_from_row(row) for row in rows]


def list_latest_observations(
    db_path: str | Path = DEFAULT_DATABASE_PATH,
    limit: int = 100,
) -> list[ListingSnapshot]:
    """Return the latest saved observation for each item, newest first."""
    if limit < 1:
        raise ValueError("limit must be at least 1")

    with _database_connection(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            SELECT {_SNAPSHOT_COLUMNS}
            FROM listing_snapshots AS snapshot
            WHERE snapshot.id = (
                SELECT candidate.id
                FROM listing_snapshots AS candidate
                WHERE candidate.item_id = snapshot.item_id
                ORDER BY candidate.observed_at DESC, candidate.id DESC
                LIMIT 1
            )
            ORDER BY snapshot.observed_at DESC, snapshot.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_snapshot_from_row(row) for row in rows]


def _snapshot_from_row(row: sqlite3.Row) -> ListingSnapshot:
    return ListingSnapshot(
        item_id=row["item_id"],
        title=row["title"],
        listing_url=row["listing_url"],
        price=Decimal(row["price"]),
        currency=row["currency"],
        condition=row["condition"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        source=row["source"],
    )
