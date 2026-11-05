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
import threading
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, ContextManager, Iterator, Protocol

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
    # Whether its feature flag is on. Off, its stored state can hold nothing up.
    is_on: Callable[[], bool] = lambda: True
    # Its budget doesn't say an account's type or, always, its currency: the review settles them.
    asks_type_and_currency: bool = False
    # Its two-way sync exists, so an import may turn it on.
    syncs: bool = True
    # What a person reads, where the name alone doesn't carry the sentence.
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


def _others() -> list[BudgetApp]:
    me = current().key
    return [app for app in all_apps() if app.key != me]


def other_sync_on() -> BudgetApp | None:
    """Another app whose two-way sync is enabled. One whose flag is off isn't running anything,
    and has no way left to be turned off."""
    return next(
        (a for a in _others() if a.is_on() and a.stores.state().doc("state").get("enabled")), None
    )


def other_running() -> bool:
    """Another app has an import run, or a recorded sync run, in progress."""
    return any(import_runs.has_running(a.key) for a in _others())
