"""Stores behind the Actual sync (tables ``actual_sync_*``, ``backend/db/tables.py``).

The same dumb storage as YNAB's, in Actual's own tables: nothing of one app's reaches the other's.
Bookkeeping only, never a credential, and not checkpointed."""

from __future__ import annotations

from backend.db.tables import (
    actual_sync_baselines,
    actual_sync_conflicts,
    actual_sync_mirror,
    actual_sync_state,
)
from backend.db.ynab_sync_store import (
    SyncBaselineStore,
    SyncConflictStore,
    SyncMirrorStore,
    SyncStateStore,
)


class ActualSyncStateStore(SyncStateStore):
    def __init__(self, engine=None):
        super().__init__(actual_sync_state, engine=engine)


class ActualMirrorStore(SyncMirrorStore):
    def __init__(self, engine=None):
        super().__init__(actual_sync_mirror, engine=engine)


class ActualBaselineStore(SyncBaselineStore):
    def __init__(self, engine=None):
        super().__init__(actual_sync_baselines, engine=engine)


class ActualConflictStore(SyncConflictStore):
    def __init__(self, engine=None):
        super().__init__(actual_sync_conflicts, engine=engine)
