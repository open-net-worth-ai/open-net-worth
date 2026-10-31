"""The write_log replay's row steps — every touched row back to its before-image.

A row the turn only EDITED is UPDATED in place, never deleted and re-inserted: a delete
fires every foreign key pointing at it from rows the turn never touched — RESTRICT fails
the whole restore (a transaction on an edited account), CASCADE silently takes the
children with it (an edited transaction's external refs, an edited story's timeline).

Four steps, one caller-held transaction:
  1. park   — rows that exist now get a temporary value in one column of each unique
              index (``seq``…), so no restored value can collide with a row still in flight
              (a turn that PERMUTED order swapped two rows' seqs);
  2. insert — rows the turn deleted, PARENT tables first;
  3. update — rows the turn edited, PARENT tables first (every pre-turn parent exists again);
  4. delete — rows the turn created, CHILD tables first (no restored row points at them).
"""

from __future__ import annotations

from itertools import count

from sqlalchemy import Integer, UniqueConstraint, select

# A parked integer sits at -(2**30 + n): inside Postgres INTEGER, far from any real seq/idx.
_PARK_INT_BASE = 2**30
_PARK_TEXT = "~restore-park:"
_CHUNK = 500

Images = list[tuple[str, dict | None]]


def apply_images(conn, by_table: dict[str, Images], table_order: list[str]) -> None:
    from backend.db import tables as db_tables

    tables = {name: db_tables.metadata.tables[name] for name in table_order}
    present = {name: _present_pks(conn, tables[name], by_table[name]) for name in table_order}

    counter = count(1)
    for name in table_order:
        for col in _park_columns(tables[name]):
            for pk in present[name]:
                _update(conn, tables[name], pk, {col.name: _parked(col, next(counter))})
    for name in table_order:
        for pk, before in by_table[name]:
            if before is not None and pk not in present[name]:
                conn.execute(tables[name].insert().values(**before))
    for name in table_order:
        for pk, before in by_table[name]:
            if before is not None and pk in present[name]:
                _update(conn, tables[name], pk, before)
    for name in reversed(table_order):
        for pk, before in by_table[name]:
            if before is None and pk in present[name]:
                conn.execute(tables[name].delete().where(_pk_match(tables[name], pk)))


def _present_pks(conn, table, images: Images) -> set[str]:
    from ._restore import _pk_param, _single_pk_col

    pk_col = _single_pk_col(table)
    pks = [pk for pk, _ in images]
    found: set[str] = set()
    for i in range(0, len(pks), _CHUNK):
        params = [_pk_param(pk_col, pk) for pk in pks[i : i + _CHUNK]]
        found.update(str(v) for (v,) in conn.execute(select(pk_col).where(pk_col.in_(params))))
    return found


def _park_columns(table) -> list:
    """One column per unique index/constraint (besides the pk) to move aside — a unique set
    is satisfied once any one of its columns differs. Never a foreign key (``book_id``,
    ``txn_id``): parking must not point a row at a parent that doesn't exist."""
    uniques = [ix.columns for ix in table.indexes if ix.unique]
    uniques += [c.columns for c in table.constraints if isinstance(c, UniqueConstraint)]
    uniques += [[c] for c in table.columns if c.unique]
    picked: dict[str, object] = {}
    for cols in uniques:
        cols = list(cols)
        if all(c.primary_key for c in cols):
            continue
        candidates = [c for c in cols if not c.primary_key and not c.foreign_keys]
        if candidates:
            picked.setdefault(candidates[0].name, candidates[0])
    return list(picked.values())


def _parked(col, n: int):
    return -(_PARK_INT_BASE + n) if isinstance(col.type, Integer) else f"{_PARK_TEXT}{n}"


def _pk_match(table, pk: str):
    from ._restore import _pk_param, _single_pk_col

    pk_col = _single_pk_col(table)
    return pk_col == _pk_param(pk_col, pk)


def _update(conn, table, pk: str, values: dict) -> None:
    conn.execute(table.update().where(_pk_match(table, pk)).values(**values))
