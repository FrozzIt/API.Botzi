from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from proxy_api.configuration.loader import ClientConfigError, load_client_config

VALID_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$tTmUnlaKhiPS0brIMzMb7w$"
    "VC5f0Rys5eemOFxeaWGvWqmSVUsJHiMEL66bz86AnnI"
)


def valid_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "revision": 1,
        "clients": {
            "client_a": {
                "enabled": True,
                "login": "client_a",
                "password_hash": VALID_HASH,
                "provider_account": "primary",
                "project_id": 10001,
                "allowed_staff_ids": [101, 102],
                "main_owner_display_name": "Owner A",
            },
            "client_b": {
                "enabled": True,
                "login": "client_b",
                "password_hash": VALID_HASH,
                "provider_account": "primary",
                "project_id": 10002,
                "allowed_staff_ids": [201],
                "main_owner_display_name": "Owner B",
            },
        },
    }


def write_yaml(path: Path, document: dict[str, object]) -> Path:
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_valid_config_is_loaded_with_digest(tmp_path: Path) -> None:
    loaded = load_client_config(
        write_yaml(tmp_path / "clients.yaml", valid_document()),
        frozenset({"primary"}),
    )

    assert loaded.document.revision == 1
    assert set(loaded.document.clients) == {"client_a", "client_b"}
    assert len(loaded.content_digest) == 64


def test_shared_project_requires_explicit_same_access_group(tmp_path: Path) -> None:
    document = valid_document()
    clients = document["clients"]
    assert isinstance(clients, dict)
    clients["client_b"]["project_id"] = 10001

    with pytest.raises(ClientConfigError):
        load_client_config(
            write_yaml(tmp_path / "independent.yaml", document),
            frozenset({"primary"}),
        )

    clients["client_a"]["access_group"] = "shared_company"
    clients["client_b"]["access_group"] = "shared_company"
    loaded = load_client_config(
        write_yaml(tmp_path / "grouped.yaml", document),
        frozenset({"primary"}),
    )
    assert loaded.document.clients["client_a"].access_group == "shared_company"


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_top_level",
        "unsupported_schema",
        "invalid_revision",
        "duplicate_login",
        "bad_project_id",
        "bad_password_hash",
        "unknown_account",
        "duplicate_staff_id",
        "unknown_client_key",
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, mutation: str) -> None:
    document = deepcopy(valid_document())
    clients = document["clients"]
    assert isinstance(clients, dict)

    if mutation == "unknown_top_level":
        document["unexpected"] = True
    elif mutation == "unsupported_schema":
        document["schema_version"] = 2
    elif mutation == "invalid_revision":
        document["revision"] = 0
    elif mutation == "duplicate_login":
        clients["client_b"]["login"] = "CLIENT_A"
    elif mutation == "bad_project_id":
        clients["client_a"]["project_id"] = 0
    elif mutation == "bad_password_hash":
        clients["client_a"]["password_hash"] = "not-an-argon2id-hash"
    elif mutation == "unknown_account":
        clients["client_a"]["provider_account"] = "missing"
    elif mutation == "duplicate_staff_id":
        clients["client_a"]["allowed_staff_ids"] = [101, 101]
    elif mutation == "unknown_client_key":
        clients["client_a"]["unexpected"] = "value"

    with pytest.raises(ClientConfigError):
        load_client_config(
            write_yaml(tmp_path / f"{mutation}.yaml", document),
            frozenset({"primary"}),
        )


def test_duplicate_yaml_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        f"""
schema_version: 1
revision: 1
revision: 2
clients:
  client_a:
    enabled: true
    login: client_a
    password_hash: {VALID_HASH}
    provider_account: primary
    project_id: 10001
    allowed_staff_ids: []
    main_owner_display_name: Owner
""",
        encoding="utf-8",
    )

    with pytest.raises(ClientConfigError):
        load_client_config(path, frozenset({"primary"}))
