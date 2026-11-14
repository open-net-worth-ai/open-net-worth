"""Which budget app the import and the two-way sync are working for, as one description of it.

The engine reads and writes YNAB's JSON shape; that shape is its internal format, and another app
is another reader and client that speak it. The current app is held in a ``ContextVar`` defaulting
to YNAB, read at call time (never at import), so existing callers and tests keep their meaning.
A ``ContextVar`` does not cross ``ThreadPoolExecutor.submit``: a worker runs through
``contextvars.copy_context().run``.

``from_ynab`` / ``to_ynab``, ``keep: "ynab"``, ``held_ynab`` and a pair's ``"ynab"`` key keep that
spelling for every app, in stored documents and on the wire: they mean "the budget app's side".
People only ever read ``app.name``.

Beside the engine, below it: ``ynab_migration`` and ``ynab_sync`` import this module, so what it
needs of them is imported on the call."""

from __future__ import annotations

import contextvars
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, ContextManager, Iterator, Protocol

from backend.core.errors import ConflictError
from backend.db.ynab_config_store import YnabConfigStore
from backend.db.ynab_sync_store import (
    YnabBaselineStore,
    YnabConflictStore,
    YnabMirrorStore,
    YnabSyncStateStore,
)
from backend.services import import_runs, ynab_flag

if TYPE_CHECKING:
    from backend.services.ynab_sync.client import Budget

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Capabilities:
    """Where the apps behave differently."""

    future_dates: bool
    edit_splits: bool
    hide_categories: bool
    income_categories: bool
    card_payment_categories: bool
    compares_ready_to_assign: bool
    zero_based_on_fresh_book: bool
    request_cap: int
    category_request_cap: int
    flag_trail: bool
    # What settling one review item may spend: a PATCH, its note and a DELETE, at most.
    resolve_request_cap: int = 6
    # A transfer between a budget account and another keeps its category on the budget leg.
    categorized_transfers: bool = False
    # The server reports what changed since a cursor that stays put when a run fails. Without one,
    # a later read compares with the mirror, so what a failed run read must be carried forward.
    delta_read: bool = True
    # It stores text its own way (a payee found whatever its case, text as long as the request
    # takes), so two sides that differ only by that haven't changed anything.
    normalises_text: bool = False
    # What it says of a row's category is the row's category: no rule or payee default fills a
    # blank it sent.
    labels_as_given_on_pull: bool = False
    # A ledger row carrying its ids is that row, even with no baseline yet: a run was killed after
    # writing the link and before saving what it had agreed.
    links_without_baseline: bool = False
    # A book can hold another budget's accounts from the same app: rows on those aren't this
    # budget's, so their absence from a read says nothing, and they are left alone.
    budget_scoped_accounts: bool = False
    # A duplicated or restored budget keeps its row ids under a new file id, so the first read
    # after a switch can't tell a row deleted there from one added here since the copy: every
    # removal it finds is held for review.
    copies_keep_ids: bool = False
    # Its connector holds a budget too: the one to import next. A sync moved to a budget points
    # the connector at it, or the page would warn about the budget the person just chose.
    connector_follows_sync: bool = False
    # It has no tag field and keeps tags in the note as #name: they become the row's tags here.
    memo_tags: bool = False


@dataclass(frozen=True)
class SyncStores:
    """The sync's four stores, each a zero-argument factory."""

    state: Callable[[], Any]
    mirror: Callable[[], Any]
    baselines: Callable[[], Any]
    conflicts: Callable[[], Any]


class Reader(Protocol):
    """Reads one budget in the engine's internal format (``ynab_migration.source``). ``fetch_plan``
    may add what its format has no place for: ``currency``, ``budget_mode``, ``split_gaps``,
    ``held``, ``notices`` (``ynab_migration.settle``)."""

    def list_plans(self, store) -> list[dict]: ...

    def fetch_plan(
        self, store, plan_id: str, *, with_budget: bool, progress: Callable = ...
    ) -> dict: ...

    def fetch_accounts(self, store, plan_id: str) -> list[dict]: ...

    def fetch_month(self, store, plan_id: str, month: str) -> dict: ...

    def currency_of(self, store, plan_id: str) -> str | None:
        """The budget's currency, where ``list_plans`` can't say it."""
        ...


@dataclass(frozen=True)
class BudgetApp:
    key: str
    name: str
    source: str
    surface: str
    caps: Capabilities
    config_store: Callable[[], Any]
    reader: Reader
    open_client: Callable[[str, "Budget"], ContextManager[Any]]
    stores: SyncStores
    lock: threading.Lock
    pool: ThreadPoolExecutor  # where its background sync runs go
    # Whether it can run here: its flag is on and nothing refuses it. Off, its stored state can
    # hold nothing up.
    is_on: Callable[[], bool] = lambda: True
    # Its connector is paused, so a tick leaves it alone. Not the same as off: its sync is still
    # on, and still holds another app's off.
    paused: Callable[[], bool] = lambda: False
    # Its runs stay off the scheduler's tick, so one that hangs holds up no other app, and it gives
    # way to another app's sync if a book ends up with both on.
    defers: bool = False
    # Its budget doesn't say an account's type or, always, its currency: the review settles them.
    asks_type_and_currency: bool = False
    # Its two-way sync exists, so an import may turn it on.
    syncs: bool = True
    # What a person reads, where the name alone doesn't carry the sentence.
    article: str = "A"
    plan_word: str = "plan"
    sign_in: str = "a YNAB access token"
    saved_sign_in: str = "token"
    run_busy: str = "A ynab run is already in progress."


class _YnabReader:
    def list_plans(self, store) -> list[dict]:
        return _ynab_source().list_plans(store)

    def fetch_plan(
        self, store, plan_id: str, *, with_budget: bool, progress: Callable = lambda *_: None
    ) -> dict:
        return _ynab_source().fetch_plan(store, plan_id, with_budget=with_budget, progress=progress)

    def fetch_accounts(self, store, plan_id: str) -> list[dict]:
        return _ynab_source().fetch_accounts(store, plan_id)

    def fetch_month(self, store, plan_id: str, month: str) -> dict:
        return _ynab_source().fetch_month(store, plan_id, month)

    def currency_of(self, store, plan_id: str) -> str | None:
        return None


def _ynab_source():
    from backend.services.ynab_migration import source

    return source


def _open_ynab_client(plan_id: str, budget: "Budget") -> ContextManager[Any]:
    from backend.services.ynab_sync.client import YnabClient

    return nullcontext(YnabClient(plan_id, budget))


YNAB = BudgetApp(
    key="ynab",
    name="YNAB",
    source="ynab",
    surface="ynab_sync",
    caps=Capabilities(
        future_dates=False,
        edit_splits=False,
        hide_categories=False,
        income_categories=False,
        card_payment_categories=True,
        compares_ready_to_assign=True,
        zero_based_on_fresh_book=True,
        request_cap=40,
        category_request_cap=20,
        flag_trail=True,
    ),
    config_store=YnabConfigStore,
    reader=_YnabReader(),
    open_client=_open_ynab_client,
    stores=SyncStores(
        state=YnabSyncStateStore,
        mirror=YnabMirrorStore,
        baselines=YnabBaselineStore,
        conflicts=YnabConflictStore,
    ),
    lock=threading.Lock(),
    pool=ThreadPoolExecutor(max_workers=1, thread_name_prefix="ynab-sync"),
    is_on=ynab_flag.on,
)

_current: contextvars.ContextVar[BudgetApp] = contextvars.ContextVar("budget_app", default=YNAB)


def _actual() -> BudgetApp:
    from backend.services.actual_sync.app import ACTUAL

    return ACTUAL


# Loaded on first look: ``actual_sync`` is built on the engine, which imports this module.
_apps: dict[str, BudgetApp | Callable[[], BudgetApp]] = {YNAB.key: YNAB, "actual_budget": _actual}


def current() -> BudgetApp:
    return _current.get()


@contextmanager
def using(app: BudgetApp) -> Iterator[BudgetApp]:
    token = _current.set(app)
    try:
        yield app
    finally:
        _current.reset(token)


def register(app: BudgetApp) -> None:
    _apps[app.key] = app


def all_apps() -> list[BudgetApp]:
    return [app() if callable(app) else app for app in _apps.values()]


def syncing_apps(on_failure: Callable[[Exception], None] = lambda _exc: None) -> list[BudgetApp]:
    """Those whose two-way sync exists and that can run here: what a scheduled tick goes through.
    One that can't be loaded or asked is left out and reported, and stops no other."""
    found = []
    for entry in list(_apps.values()):
        try:
            app = entry() if callable(entry) else entry
            if app.syncs and app.is_on() and not app.paused():
                found.append(app)
        except Exception as exc:
            on_failure(exc)
    return found


def _others() -> list[BudgetApp]:
    """An app that can't be loaded has nothing running, and holds no other up."""
    me = current().key
    found = []
    for entry in list(_apps.values()):
        try:
            app = entry() if callable(entry) else entry
        except Exception:
            logger.exception("a budget app could not be loaded")
            continue
        if app.key != me:
            found.append(app)
    return found


def other_sync_on() -> BudgetApp | None:
    """Another app whose two-way sync is enabled. One whose flag is off isn't running anything,
    and has no way left to be turned off."""
    return next(
        (a for a in _others() if a.is_on() and a.stores.state().doc("state").get("enabled")), None
    )


def other_running() -> bool:
    """Another app has an import run, or a recorded sync run, in progress; a preview or a verify
    writes nothing, so it stands in nobody's way. A scheduled run has no record, and needs none
    here: its sync is on, and stays on until it ends."""
    return any(import_runs.has_writing_run(a.key) for a in _others())


_starting = threading.Lock()


@contextmanager
def starting() -> Iterator[None]:
    """Held while "nothing of another app stands in the way" is checked and the run is started or
    the sync turned on: two apps starting together would otherwise each find the other idle."""
    with _starting:
        yield


def refuse_while_others_run(then: str) -> None:
    if other_running():
        names = " or ".join(a.name for a in _others())
        raise ConflictError(f"An import or sync with {names} is running; {then} when it finishes.")


def refuse_beside_others(before: str, then: str) -> None:
    """``before`` and ``then``: what the person is doing, for the two sentences."""
    if other := other_sync_on():
        raise ConflictError(
            f"The {other.name} sync is on. Turn it off in Data Sync before {before}."
        )
    refuse_while_others_run(then)
