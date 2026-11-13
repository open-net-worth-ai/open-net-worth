"""``202611131200_bank_syncs_use_book_transactions``: a saved bank-sync Automation still carrying
an old template's instructions word for word books through the ``book-transactions`` skill
after the upgrade; one its owner edited keeps its text exactly; downgrade puts the old text back.

A sync Automation stores its own copy of the template text, so moving the booking rules into a
skill would otherwise never reach an automation that already existed (2026-11-13).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import create_engine

from backend.db.alembic_config import make_config
from tests.db.conftest import patch_all_cutover_sources_missing

_BEFORE = "202611061200_lots_txn_id_foreign_key"
_AFTER = "202611131200_bank_syncs_use_book_transactions"

_books = sa.table("books", sa.column("id", sa.Text), sa.column("name", sa.Text))
_automations = sa.table(
    "automations",
    sa.column("book_id", sa.Text),
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)


def _texts() -> tuple[tuple[str, str], ...]:
    path = (
        Path(__file__).resolve().parents[2]
        / "backend/db/alembic/versions/202611131200_bank_syncs_use_book_transactions.py"
    )
    spec = importlib.util.spec_from_file_location("bank_sync_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._TEMPLATE_TEXTS


@pytest.fixture
def before(db_url, monkeypatch, tmp_path):
    from backend.db import engine as eng

    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    patch_all_cutover_sources_missing(monkeypatch, tmp_path)
    eng.dispose_engine()
    command.upgrade(make_config(db_url), _BEFORE)
    yield db_url
    eng.dispose_engine()


def _seed(url: str, rows: dict[str, str]) -> None:
    engine = create_engine(url)
    with engine.begin() as c:
        if not c.execute(sa.select(_books.c.id).where(_books.c.id == "book-1")).first():
            c.execute(_books.insert().values(id="book-1", name="Book"))
        for seq, (row_id, instructions) in enumerate(rows.items(), start=100):
            c.execute(
                _automations.insert().values(
                    book_id="book-1",
                    seq=seq,
                    id=row_id,
                    payload={"id": row_id, "name": row_id, "instructions": instructions},
                )
            )
    engine.dispose()


def _instructions(url: str) -> dict[str, str]:
    engine = create_engine(url)
    with engine.connect() as c:
        rows = c.execute(sa.select(_automations.c.id, _automations.c.payload)).all()
    engine.dispose()
    return {row_id: payload["instructions"] for row_id, payload in rows}


def test_unedited_syncs_move_to_the_skill_and_edited_ones_are_untouched(before):
    texts = _texts()
    assert len(texts) == 7
    old_gc, new_gc = next((o, n) for o, n in texts if "GoCardless" in o)
    edited = old_gc.replace("Message me only", "Always message me, not only")
    _seed(
        before,
        {
            **{f"sync-{i}": old for i, (old, _new) in enumerate(texts)},
            "edited": edited,
            "unrelated": "Send me a weekly summary.",
        },
    )

    command.upgrade(make_config(before), _AFTER)

    after = _instructions(before)
    for i, (_old, new) in enumerate(texts):
        assert after[f"sync-{i}"] == new
        assert "book-transactions" in new
    assert after["edited"] == edited
    assert after["unrelated"] == "Send me a weekly summary."
    assert "book-transactions" in new_gc


def test_downgrade_puts_the_old_text_back(before):
    texts = _texts()
    _seed(before, {f"sync-{i}": old for i, (old, _new) in enumerate(texts)})
    command.upgrade(make_config(before), _AFTER)
    command.downgrade(make_config(before), _BEFORE)
    back = _instructions(before)
    assert [back[f"sync-{i}"] for i in range(len(texts))] == [old for old, _ in texts]
