import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.listing_history import (
    ListingSnapshot,
    get_item_price_history,
    initialize_database,
    list_latest_observations,
    save_listing_snapshot,
)


def make_snapshot(
    item_id: str,
    price: str,
    observed_at: datetime,
) -> ListingSnapshot:
    return ListingSnapshot(
        item_id=item_id,
        title=f"Synthetic listing {item_id}",
        listing_url=f"https://example.invalid/items/{item_id}",
        price=Decimal(price),
        currency="USD",
        condition="Used",
        observed_at=observed_at,
        source="synthetic-test",
    )


def test_initialize_database_creates_database_and_table(tmp_path):
    database_path = tmp_path / "nested" / "history.sqlite3"

    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'listing_snapshots'
            """
        ).fetchone()
    assert table == ("listing_snapshots",)


def test_initialize_database_is_idempotent(tmp_path):
    database_path = tmp_path / "history.sqlite3"

    initialize_database(database_path)
    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = connection.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name = 'listing_snapshots'
            """
        ).fetchone()[0]
        indexes = connection.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'index' AND name = 'idx_listing_snapshots_item_observed'
            """
        ).fetchone()[0]
    assert tables == 1
    assert indexes == 1


def test_repeated_observations_are_preserved(tmp_path):
    database_path = tmp_path / "history.sqlite3"
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = make_snapshot("synthetic-1", "19.95", timestamp)
    second = make_snapshot("synthetic-1", "18.50", timestamp.replace(day=2))

    first_id = save_listing_snapshot(first, database_path)
    second_id = save_listing_snapshot(second, database_path)

    history = get_item_price_history("synthetic-1", database_path)
    assert first_id != second_id
    assert len(history) == 2
    assert [entry.price for entry in history] == [
        Decimal("19.95"),
        Decimal("18.50"),
    ]


def test_price_history_is_chronological_and_exact(tmp_path):
    database_path = tmp_path / "history.sqlite3"
    earlier = make_snapshot(
        "synthetic-2",
        "10.10",
        datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    later = make_snapshot(
        "synthetic-2",
        "10.20",
        datetime(2026, 2, 3, tzinfo=timezone.utc),
    )
    save_listing_snapshot(later, database_path)
    save_listing_snapshot(earlier, database_path)

    history = get_item_price_history("synthetic-2", database_path)

    assert [entry.price for entry in history] == [
        Decimal("10.10"),
        Decimal("10.20"),
    ]
    assert str(history[0].price) == "10.10"
    assert [entry.observed_at for entry in history] == [
        earlier.observed_at,
        later.observed_at,
    ]
    assert history[0].currency == "USD"
    assert history[0].condition == "Used"
    assert history[0].source == "synthetic-test"


def test_timestamp_is_normalized_to_utc():
    local_timestamp = datetime.fromisoformat("2026-02-01T12:00:00+05:30")

    snapshot = make_snapshot("synthetic-time", "10.10", local_timestamp)

    assert snapshot.observed_at == datetime(
        2026, 2, 1, 6, 30, tzinfo=timezone.utc
    )
    assert snapshot.observed_at.utcoffset().total_seconds() == 0


def test_invalid_iso_currency_code_is_rejected():
    with pytest.raises(ValueError, match="unknown ISO 4217 currency code"):
        ListingSnapshot(
            item_id="synthetic-invalid-currency",
            title="Synthetic listing",
            listing_url="https://example.invalid/items/invalid",
            price=Decimal("1.00"),
            currency="ZZZ",
        )


def test_latest_observations_returns_one_per_item(tmp_path):
    database_path = tmp_path / "history.sqlite3"
    save_listing_snapshot(
        make_snapshot(
            "synthetic-3",
            "5.00",
            datetime(2026, 3, 1, tzinfo=timezone.utc),
        ),
        database_path,
    )
    latest_item_3 = make_snapshot(
        "synthetic-3",
        "4.50",
        datetime(2026, 3, 2, tzinfo=timezone.utc),
    )
    latest_item_4 = make_snapshot(
        "synthetic-4",
        "9.00",
        datetime(2026, 3, 3, tzinfo=timezone.utc),
    )
    save_listing_snapshot(latest_item_3, database_path)
    save_listing_snapshot(latest_item_4, database_path)

    latest = list_latest_observations(database_path)

    assert [(entry.item_id, entry.price) for entry in latest] == [
        ("synthetic-4", Decimal("9.00")),
        ("synthetic-3", Decimal("4.50")),
    ]


def test_history_for_unknown_item_is_empty(tmp_path):
    database_path = tmp_path / "history.sqlite3"
    initialize_database(database_path)

    assert get_item_price_history("not-present", database_path) == []


def test_failed_insert_rolls_back_and_closes_connections(tmp_path, monkeypatch):
    database_path = tmp_path / "history.sqlite3"
    initialize_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_snapshot
            BEFORE INSERT ON listing_snapshots
            BEGIN
                SELECT RAISE(ABORT, 'synthetic insert failure');
            END
            """
        )

    original_connect = sqlite3.connect
    connections = []

    def tracking_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr("src.listing_history.sqlite3.connect", tracking_connect)

    with pytest.raises(sqlite3.IntegrityError, match="synthetic insert failure"):
        save_listing_snapshot(
            make_snapshot(
                "synthetic-failure",
                "12.34",
                datetime(2026, 4, 1, tzinfo=timezone.utc),
            ),
            database_path,
        )

    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")

    with original_connect(database_path) as connection:
        saved_rows = connection.execute(
            """
            SELECT COUNT(*) FROM listing_snapshots
            WHERE item_id = 'synthetic-failure'
            """
        ).fetchone()[0]
    assert saved_rows == 0
