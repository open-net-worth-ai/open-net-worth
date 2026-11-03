"""Institution lifecycle keeps account records while managing their shared label."""

import pytest

from backend.core.errors import ConflictError
from backend.db.account_store import AccountStore
from backend.db.institution_store import InstitutionStore
from backend.services import institutions as service


def _account(id: str, institution_id: str | None = None) -> dict:
    return {
        "id": id,
        "name": id,
        "type": "cash",
        "currency": "SGD",
        "institution_id": institution_id,
    }


def test_rename_institution_updates_its_shared_name(db_isolated):
    institution = InstitutionStore().resolve_or_create("DBS")
    AccountStore().insert(_account("acc_one", institution["id"]))

    renamed = service.rename_institution(institution["id"], "DBS Bank")

    assert renamed == {
        "id": institution["id"],
        "name": "DBS Bank",
        "account_count": 1,
        "account_names": ["acc_one"],
    }
    assert InstitutionStore().find_by_normalized_name("dbs bank")["id"] == institution["id"]


def test_rename_institution_refuses_to_merge_two_existing_institutions(db_isolated):
    first = InstitutionStore().resolve_or_create("DBS")
    InstitutionStore().resolve_or_create("UOB")

    with pytest.raises(ConflictError, match="already exists"):
        service.rename_institution(first["id"], "UOB")


def test_delete_institution_unlinks_accounts_without_deleting_them(db_isolated):
    institution = InstitutionStore().resolve_or_create("DBS")
    AccountStore().insert(_account("acc_one", institution["id"]))
    AccountStore().insert(_account("acc_two", institution["id"]))

    deleted = service.delete_institution(institution["id"])

    assert deleted == {
        "id": institution["id"],
        "unlinked_account_count": 2,
        "unlinked_account_ids": ["acc_one", "acc_two"],
    }
    assert InstitutionStore().find_by_id(institution["id"]) is None
    assert [AccountStore().find_by_id(id)["institution_id"] for id in ("acc_one", "acc_two")] == [
        None,
        None,
    ]


def test_list_names_each_institutions_accounts_sorted(db_isolated):
    chase = InstitutionStore().resolve_or_create("Chase")
    AccountStore().insert({**_account("acc_b", chase["id"]), "name": "savings"})
    AccountStore().insert({**_account("acc_a", chase["id"]), "name": "Checking"})
    AccountStore().insert(_account("acc_none"))

    (listed,) = service.list_institutions()

    assert listed["account_count"] == 2
    assert listed["account_names"] == ["Checking", "savings"]
