"""Stores behind the Actual import (table ``actual_sync_baselines``, ``backend/db/tables.py``).

The same dumb storage as YNAB's, in Actual's own table: nothing of one app's reaches the other's.
Bookkeeping only, never a credential, and not checkpointed."""

from __future__ import annotations

from backend.db.tables import actual_sync_baselines
from backend.db.ynab_sync_store import YnabBaselineStore


class ActualBaselineStore(YnabBaselineStore):
    TABLE = actual_sync_baselines
