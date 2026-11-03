"""Shared institution lifecycle operations."""

from __future__ import annotations

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.account_store import AccountStore
from backend.db.engine import locked_unit_of_work
from backend.db.institution_store import InstitutionStore
from backend.db.tables import accounts as accounts_table
from backend.db.tables import institutions as institutions_table


def _names_by_institution(accounts: list[dict]) -> dict[str, list[str]]:
    """Each institution's account names, sorted — so the manager shows WHICH accounts a rename
    or delete touches, not just how many."""
    names: dict[str, list[str]] = {}
    for account in accounts:
        if institution_id := account.get("institution_id"):
            names.setdefault(institution_id, []).append(str(account.get("name") or "Unnamed"))
    return {key: sorted(value, key=str.casefold) for key, value in names.items()}


def _shape(institution: dict, names: dict[str, list[str]]) -> dict:
    own = names.get(institution["id"], [])
    return {
        "id": institution["id"],
        "name": institution["name"],
        "account_count": len(own),
        "account_names": own,
    }


def list_institutions() -> list[dict]:
    names = _names_by_institution(AccountStore().all())
    return [
        _shape(institution, names)
        for institution in sorted(InstitutionStore().all(), key=lambda row: row["name"].casefold())
    ]


def rename_institution(institution_id: str, name: object) -> dict:
    display_name = " ".join(str(name or "").split())
    if not display_name:
        raise ValidationError("Institution name cannot be empty")
    with locked_unit_of_work(accounts_table, institutions_table) as conn:
        institutions = InstitutionStore()
        current = institutions.find_by_id(institution_id, conn=conn)
        if current is None:
            raise NotFoundError("Institution not found")
        duplicate = institutions.find_by_normalized_name(display_name, conn=conn)
        if duplicate is not None and duplicate["id"] != institution_id:
            raise ConflictError(f"Institution {duplicate['name']!r} already exists")
        renamed = institutions.update_by_id(institution_id, {"name": display_name}, conn=conn)
        names = _names_by_institution(AccountStore().all(conn=conn))
    return _shape(renamed, names)


def delete_institution(institution_id: str) -> dict:
    with locked_unit_of_work(accounts_table, institutions_table) as conn:
        institutions = InstitutionStore()
        if institutions.find_by_id(institution_id, conn=conn) is None:
            raise NotFoundError("Institution not found")
        account_store = AccountStore()
        linked_ids = [
            account["id"]
            for account in account_store.all(conn=conn)
            if account.get("institution_id") == institution_id
        ]
        account_store.bulk_patch(
            {account_id: {"institution_id": None} for account_id in linked_ids}, conn=conn
        )
        institutions.delete(institution_id, conn=conn)
    return {
        "id": institution_id,
        "unlinked_account_count": len(linked_ids),
        "unlinked_account_ids": linked_ids,
    }
