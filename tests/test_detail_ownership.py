import pytest

from proxy_api.database import ContactDetailOwnership
from proxy_api.reads.detail_ownership import DetailOwnershipConflict, normalize_identifier


def test_detail_ownership_table_stores_only_relation_metadata() -> None:
    assert set(ContactDetailOwnership.__table__.columns.keys()) == {
        "provider_account",
        "detail_id",
        "contact_id",
        "project_id",
        "active",
        "verified_at",
    }


@pytest.mark.parametrize("value", ["", " 1", "1 ", "../contacts", "1?token=secret", "a/b"])
def test_ownership_identifier_cannot_change_provider_path(value: str) -> None:
    with pytest.raises(DetailOwnershipConflict):
        normalize_identifier(value)
