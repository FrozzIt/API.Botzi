from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import yaml
from argon2 import Type, extract_parameters
from argon2.exceptions import InvalidHashError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CLIENT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_CONFIG_BYTES = 1_048_576


class ClientConfigError(ValueError):
    """Raised when a client configuration cannot be safely accepted."""


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ClientConfigError("Configuration contains a non-scalar mapping key") from exc
        if duplicate:
            raise ClientConfigError("Configuration contains a duplicate mapping key")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class ClientDefinition(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    enabled: bool
    login: str = Field(min_length=1, max_length=128)
    password_hash: str = Field(min_length=32, max_length=512)
    provider_account: str = Field(min_length=1, max_length=64)
    project_id: int = Field(gt=0, strict=True)
    access_group: str | None = Field(default=None, min_length=1, max_length=64)
    allowed_staff_ids: list[int] = Field(default_factory=list, max_length=10_000)
    main_owner_display_name: str = Field(min_length=1, max_length=128)

    @field_validator("login")
    @classmethod
    def validate_login(cls, value: str) -> str:
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("login must not contain whitespace")
        return value

    @field_validator("provider_account", "access_group")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is not None and not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("identifier contains unsupported characters")
        return value

    @field_validator("allowed_staff_ids")
    @classmethod
    def validate_staff_ids(cls, value: list[int]) -> list[int]:
        if any(isinstance(item, bool) or item <= 0 for item in value):
            raise ValueError("staff IDs must be positive integers")
        if len(value) != len(set(value)):
            raise ValueError("staff IDs must be unique")
        return value

    @field_validator("password_hash")
    @classmethod
    def validate_password_hash(cls, value: str) -> str:
        try:
            parameters = extract_parameters(value)
        except InvalidHashError as exc:
            raise ValueError("password_hash must be a valid Argon2 hash") from exc
        if parameters.type is not Type.ID:
            raise ValueError("password_hash must use Argon2id")
        return value


class ClientConfigDocument(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(strict=True)
    revision: int = Field(gt=0, strict=True)
    clients: dict[str, ClientDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_document(self) -> ClientConfigDocument:
        if self.schema_version != 1:
            raise ValueError("unsupported schema_version")

        normalized_logins: set[str] = set()
        project_groups: dict[tuple[str, int], str] = {}
        for client_key, client in self.clients.items():
            if not CLIENT_KEY_PATTERN.fullmatch(client_key):
                raise ValueError("client key contains unsupported characters")

            normalized_login = client.login.casefold()
            if normalized_login in normalized_logins:
                raise ValueError("client logins must be unique")
            normalized_logins.add(normalized_login)

            project_scope = (client.provider_account, client.project_id)
            access_group = client.access_group or f"client:{client_key}"
            existing_group = project_groups.get(project_scope)
            if existing_group is not None and existing_group != access_group:
                raise ValueError("independent clients must not share a project")
            project_groups[project_scope] = access_group
        return self


@dataclass(frozen=True)
class LoadedClientConfig:
    document: ClientConfigDocument
    content_digest: str


def load_client_config(
    path: Path,
    allowed_provider_accounts: frozenset[str],
) -> LoadedClientConfig:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ClientConfigError("Client configuration cannot be read") from exc
    if not content or len(content) > MAX_CONFIG_BYTES:
        raise ClientConfigError("Client configuration size is invalid")

    try:
        raw_document = yaml.load(content, Loader=UniqueKeyLoader)
    except (yaml.YAMLError, ClientConfigError) as exc:
        raise ClientConfigError("Client configuration YAML is invalid") from exc

    try:
        document = ClientConfigDocument.model_validate(raw_document)
    except ValidationError as exc:
        raise ClientConfigError("Client configuration schema is invalid") from exc

    referenced_accounts = {client.provider_account for client in document.clients.values()}
    if not referenced_accounts.issubset(allowed_provider_accounts):
        raise ClientConfigError("Client configuration references an unknown provider account")

    return LoadedClientConfig(
        document=document,
        content_digest=hashlib.sha256(content).hexdigest(),
    )
