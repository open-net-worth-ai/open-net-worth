"""Saved bank-sync Automations book through the ``book-transactions`` skill.

A sync Automation keeps its own copy of the template's instructions, so the bank-sync
templates moving their booking rules into the shared ``book-transactions`` skill (categorise
every row, durable payees) reached no automation already created. This rewrites a saved
automation whose instructions are EXACTLY one of the seven old template texts — never edited
by its owner — to the new one. Anything the owner changed, even by a character, is left alone.
Downgrade restores the old text where the new one is still unedited.

Revision ID: 202611131200_bank_syncs_use_book_transactions
Revises: 202611061200_lots_txn_id_foreign_key
Create Date: 2026-11-13
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "202611131200_bank_syncs_use_book_transactions"
down_revision = "202611061200_lots_txn_id_foreign_key"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_automations = sa.table("automations", sa.column("id", sa.Text), sa.column("payload", sa.JSON))

# (old template instructions, new template instructions), verbatim.
_TEMPLATE_TEXTS: tuple[tuple[str, str], ...] = (
    # sync_simplefin
    (
        "Fetch new SimpleFIN transactions and book them. Use the linked account for each one. If an account isn’t linked, ask me instead of guessing. Pick a category from the merchant or the bank’s category hint when it clearly matches one of mine and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, bring it in line, but never override something I changed by hand. If the bank withdrew a transaction it already booked, flag it and tell me. Never delete anything. Message me only when something was booked or something went wrong.",
        "Fetch new SimpleFIN transactions and book them using the book-transactions skill. Message me only when something was booked or something went wrong.",
    ),
    # sync_plaid
    (
        "Fetch new Plaid transactions and book them. Use the linked account for each one. If an account isn’t linked, ask me instead of guessing. Pick a category from the merchant or the bank’s category hint when it clearly matches one of mine and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, bring it in line, but never override something I changed by hand. If the bank withdrew a transaction it already booked, flag it and tell me. Never delete anything. Message me only when something was booked or something went wrong.",
        "Fetch new Plaid transactions and book them using the book-transactions skill. Message me only when something was booked or something went wrong.",
    ),
    # sync_lunch_money
    (
        "Fetch new Lunch Money transactions and book them. Use the linked account for each one. If an account isn’t linked, ask me instead of guessing. Pick a category from the merchant or the bank’s category hint when it clearly matches one of mine and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, bring it in line, but never override something I changed by hand. If the bank withdrew a transaction it already booked, flag it and tell me. Never delete anything. Message me only when something was booked or something went wrong.",
        "Fetch new Lunch Money transactions and book them using the book-transactions skill. Message me only when something was booked or something went wrong.",
    ),
    # sync_lunch_flow
    (
        "Fetch new Lunch Flow transactions and book them. Use the linked account for each one. If an account isn’t linked, ask me instead of guessing. Pick a category from the merchant or the bank’s category hint when it clearly matches one of mine and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, bring it in line, but never override something I changed by hand. If the bank withdrew a transaction it already booked, flag it and tell me. Never delete anything. Message me only when something was booked or something went wrong.",
        "Fetch new Lunch Flow transactions and book them using the book-transactions skill. Message me only when something was booked or something went wrong.",
    ),
    # sync_ynab
    (
        "Fetch new YNAB transactions and book them. Use the linked account for each one. If an account isn’t linked, ask me instead of guessing. Pick a category from the merchant or the bank’s category hint when it clearly matches one of mine and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, bring it in line, but never override something I changed by hand. If the bank withdrew a transaction it already booked, flag it and tell me. Never delete anything. Message me only when something was booked or something went wrong.",
        "Fetch new YNAB transactions and book them using the book-transactions skill. Message me only when something was booked or something went wrong.",
    ),
    # sync_enable_banking
    (
        "Fetch new Enable Banking transactions and book them. Use the linked account for each one. If an account isn’t linked, ask me instead of guessing. Pick a category from the merchant or the bank’s category hint when it clearly matches one of mine and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, bring it in line, but never override something I changed by hand. If the bank withdrew a transaction it already booked, flag it and tell me. Never delete anything. Message me only when something was booked or something went wrong.",
        "Fetch new Enable Banking transactions and book them using the book-transactions skill. Message me only when something was booked or something went wrong.",
    ),
    # sync_gocardless
    (
        "Fetch new GoCardless transactions and book them. Use the linked account for each one. If an account isn’t linked, ask me instead of guessing. Pick a category from the merchant or the bank’s category hint when it clearly matches one of mine and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, bring it in line, but never override something I changed by hand. If the bank withdrew a transaction it already booked, flag it and tell me. Never delete anything. Message me only when something was booked or something went wrong.",
        "Fetch new GoCardless transactions and book them using the book-transactions skill. Message me only when something was booked or something went wrong.",
    ),
)


def _rewrite(mapping: dict[str, str]) -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.select(_automations.c.id, _automations.c.payload)).all()
    changed = 0
    for row_id, payload in rows:
        if not isinstance(payload, dict):
            continue
        replacement = mapping.get(payload.get("instructions"))
        if replacement is None:
            continue
        conn.execute(
            _automations.update()
            .where(_automations.c.id == row_id)
            .values(payload={**payload, "instructions": replacement})
        )
        changed += 1
    if changed:
        logger.info("bank-sync automations rewritten: %d", changed)


def upgrade() -> None:
    _rewrite({old: new for old, new in _TEMPLATE_TEXTS})


def downgrade() -> None:
    _rewrite({new: old for old, new in _TEMPLATE_TEXTS})
