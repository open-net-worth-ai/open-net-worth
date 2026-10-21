"""The sync's YNAB client: every request goes through ``ynab_migration.source.call`` (fixed base
URL, stored Bearer token, response size cap, sanitized errors, 429 retry) and is counted against
the run's budget, so a run can never spend more of the 200-requests-an-hour allowance than it was
given. Paths are built from ids YNAB itself returned; the token is never visible here."""

from __future__ import annotations

import re

from backend.core.errors import ExternalServiceError
from backend.db.ynab_config_store import YnabConfigStore
from backend.services.ynab_migration import source

PLAN_MAX_BYTES = 96 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")


def _checked(value: str, pattern: re.Pattern = _ID) -> str:
    """Ids and months reach the URL path; refuse anything that is not plainly one."""
    if not isinstance(value, str) or not pattern.match(value):
        raise ExternalServiceError("refused to send a malformed id to YNAB")
    return value


class RateCapReached(ExternalServiceError):
    """The run has used its request budget, or YNAB answered 429; what is left waits for the
    next run. Never slept on: the run lock is held while a run waits."""

    def __init__(
        self, message: str = "YNAB request limit reached; the rest waits for the next run"
    ):
        super().__init__(message)


class Budget:
    def __init__(self, cap: int):
        self.cap, self.used = cap, 0

    def left(self) -> int:
        return self.cap - self.used

    def spend(self, n: int = 1) -> None:
        if self.used + n > self.cap:
            raise RateCapReached
        self.used += n


class YnabClient:
    def __init__(self, plan_id: str, budget: Budget):
        self.plan, self.budget, self._store = _checked(plan_id), budget, YnabConfigStore()

    def _call(self, method: str, path: str, params=None, body=None, **kw) -> dict:
        self.budget.spend()  # every actual send is charged: source.call never retries here
        try:
            return source.call(
                self._store, method, f"/plans/{self.plan}{path}", params, body, retry=False, **kw
            )
        except ExternalServiceError as exc:
            if "HTTP 429" in str(exc):
                raise RateCapReached(
                    "YNAB rate limit hit; the rest waits for the next run"
                ) from None
            raise

    def read_plan(self, knowledge: int | None) -> dict:
        """The whole plan, or only what changed since ``knowledge``: ONE request either way."""
        params = {"last_knowledge_of_server": knowledge} if knowledge is not None else None
        return self._call("GET", "", params, max_bytes=PLAN_MAX_BYTES)

    def save_transactions(self, method: str, rows: list[dict]) -> list[dict]:
        """POST (create) or PATCH (update by id) a batch; returns the saved transactions."""
        data = self._call(method, "/transactions", body={"transactions": rows})
        return data.get("transactions") or (
            [data["transaction"]] if data.get("transaction") else []
        )

    def delete_transaction(self, txn_id: str) -> None:
        self._call("DELETE", f"/transactions/{_checked(txn_id)}")

    def set_assigned(self, month: str, category_id: str, milli: int) -> None:
        self._call(
            "PATCH",
            f"/months/{_checked(month, _MONTH)}-01/categories/{_checked(category_id)}",
            body={"category": {"budgeted": milli}},
        )

    def set_note(self, category_id: str, note: str) -> dict:
        return self._call(
            "PATCH", f"/categories/{_checked(category_id)}", body={"category": {"note": note}}
        )["category"]


def rejected(exc: Exception) -> bool:
    """YNAB refused this request's content (400/404/409) — the change is not retryable as sent,
    unlike an auth failure, an outage or the rate limit, which stop the whole run."""
    return any(f"HTTP {code}" in str(exc) for code in (400, 404, 409))
