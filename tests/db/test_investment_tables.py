from sqlalchemy import inspect

from backend.db import tables as t
from backend.db.tables import DEFAULT_BOOK_ID  # noqa: F401

BOOK_INVESTMENT_TABLES = [
    "positions",
    "lots",
    "grants",
    "vest_events",
    "option_contracts",
    "realized_events",
]


def test_all_eight_investment_tables_registered():
    for name in BOOK_INVESTMENT_TABLES + ["watchlists", "asset_brief"]:
        assert name in t.metadata.tables


def test_book_investment_tables_are_book_scoped_single_pk():
    for name in BOOK_INVESTMENT_TABLES:
        table = t.metadata.tables[name]
        assert [c.name for c in table.primary_key.columns] == ["id"]  # write_log single-col PK
        promoted = {"txn_id"} if name == "lots" else set()  # the one real FK column
        assert {c.name for c in table.c} == {"book_id", "seq", "id", "payload", *promoted}
        assert "book_id" in [fk.parent.name for fk in table.foreign_keys]  # FK -> books.id


def test_watchlists_is_book_keyed_singleton():
    assert {c.name for c in t.watchlists.c} == {"book_id", "payload"}
    assert [c.name for c in t.watchlists.primary_key.columns] == ["book_id"]


def test_asset_brief_is_tier2_cache_no_book_id():
    assert "book_id" not in t.asset_brief.c  # cache, NOT Tier-1
    assert {c.name for c in t.asset_brief.c} == {"seq", "id", "payload"}
    assert [c.name for c in t.asset_brief.primary_key.columns] == ["id"]  # holds instrument_id


def test_investment_schema_creates_on_both_dialects(db_engine):
    insp = inspect(db_engine)  # SQLite here, Postgres in CI
    for name in BOOK_INVESTMENT_TABLES + ["watchlists", "asset_brief"]:
        assert insp.has_table(name)
