"""One-shot YNAB -> Open Net Worth migration (preview / import / verify as a background job).
Behind the ``ynab_import_and_sync`` flag (``services.ynab_flag``)."""

from backend.services.ynab_flag import gated
from backend.services.ynab_migration import job
from backend.services.ynab_migration.job import ConfirmationRequired

start = gated(job.start)
status = gated(job.status)

__all__ = ["ConfirmationRequired", "start", "status"]
