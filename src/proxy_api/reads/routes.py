from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from proxy_api.auth.service import AuthenticatedSession
from proxy_api.infrastructure import get_session_factory
from proxy_api.projects.routes import (
    AuthenticatedSessionDependency,
    ProviderAccountDependency,
    ProviderDependency,
    provider_unavailable_error,
)
from proxy_api.provider import (
    ProviderError,
    ProviderObjectNotFound,
    ProviderProtocolError,
)
from proxy_api.reads.detail_ownership import (
    DetailOwnership,
    DetailOwnershipConflict,
    DetailOwnershipStore,
    DetailOwnershipUnavailable,
    ProviderIdentifier,
    VerifiedContact,
)

router = APIRouter()


class ReadAccessDenied(Exception):
    """Neutral denial for a foreign, unknown, or unproven read target."""


def read_not_found_error() -> JSONResponse:
    return JSONResponse(
        {
            "status": "error",
            "errors": [{"code": 404, "message": "Object not found"}],
        }
    )


def request_not_valid_error() -> JSONResponse:
    return JSONResponse(
        {
            "status": "error",
            "errors": [{"code": 422, "message": "Request is not valid"}],
        }
    )


async def get_project_scoped_session(
    request: Request,
    access: AuthenticatedSessionDependency,
) -> AuthenticatedSession:
    requested_projects: list[str] = []
    path_project = request.path_params.get("project_id")
    if path_project is not None:
        requested_projects.append(str(path_project))

    query_projects = request.query_params.getlist("project_id")
    if len(query_projects) > 1:
        raise ReadAccessDenied
    requested_projects.extend(query_projects)

    for raw_project_id in requested_projects:
        try:
            requested_project_id = int(raw_project_id)
        except ValueError:
            raise ReadAccessDenied from None
        if str(requested_project_id) != raw_project_id or requested_project_id != access.project_id:
            raise ReadAccessDenied
    return access


ProjectScopedSessionDependency = Annotated[
    AuthenticatedSession,
    Depends(get_project_scoped_session),
]


def get_detail_ownership_store() -> DetailOwnershipStore:
    return DetailOwnershipStore(get_session_factory())


DetailOwnershipDependency = Annotated[
    DetailOwnershipStore,
    Depends(get_detail_ownership_store),
]


def _require_int(value: object) -> int:
    if type(value) is not int:
        raise ProviderProtocolError("Provider response is invalid")
    return value


def _require_identifier(value: object) -> int | str:
    if type(value) is int:
        return value
    if isinstance(value, str) and value and value == value.strip():
        return value
    raise ProviderProtocolError("Provider response is invalid")


def _identifiers_match(first: int | str, second: int | str) -> bool:
    return str(first) == str(second)


def _require_string(value: object) -> str:
    if not isinstance(value, str):
        raise ProviderProtocolError("Provider response is invalid")
    return value


def _require_bool(value: object) -> bool:
    if type(value) is not bool:
        raise ProviderProtocolError("Provider response is invalid")
    return value


def _validate_project_links(value: object, expected_project_id: int) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"project_id", "last_project_id"} and nested is not None:
                if type(nested) is not int or nested != expected_project_id:
                    raise ReadAccessDenied
            _validate_project_links(nested, expected_project_id)
    elif isinstance(value, list):
        for nested in value:
            _validate_project_links(nested, expected_project_id)


def _copy_optional_string(
    source: dict[str, object],
    target: dict[str, object],
    field: str,
) -> None:
    if field not in source:
        return
    value = source[field]
    if value is not None and not isinstance(value, str):
        raise ProviderProtocolError("Provider response is invalid")
    target[field] = value


def _copy_optional_int(
    source: dict[str, object],
    target: dict[str, object],
    field: str,
) -> None:
    if field not in source:
        return
    value = source[field]
    if value is not None and type(value) is not int:
        raise ProviderProtocolError("Provider response is invalid")
    target[field] = value


def _serialize_contact(
    value: object,
    expected_project_id: int,
    *,
    expected_contact_id: int | str | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Provider response is invalid")
    _validate_project_links(value, expected_project_id)

    contact_id = _require_identifier(value.get("id"))
    if expected_contact_id is not None and not _identifiers_match(contact_id, expected_contact_id):
        raise ReadAccessDenied
    if _require_int(value.get("project_id")) != expected_project_id:
        raise ReadAccessDenied

    details = value.get("details", [])
    if not isinstance(details, list):
        raise ProviderProtocolError("Provider response is invalid")
    public_details: list[dict[str, object]] = []
    for detail in details:
        if not isinstance(detail, dict):
            raise ProviderProtocolError("Provider response is invalid")
        public_details.append(
            {
                "id": _require_identifier(detail.get("id")),
                "type": _require_string(detail.get("type")),
                "data": _require_string(detail.get("data")),
            }
        )

    fields = value.get("fields", [])
    if not isinstance(fields, list):
        raise ProviderProtocolError("Provider response is invalid")
    public_fields: list[dict[str, object]] = []
    for field in fields:
        if not isinstance(field, dict):
            raise ProviderProtocolError("Provider response is invalid")
        public_field: dict[str, object] = {
            "id": _require_identifier(field.get("id")),
            "type": _require_string(field.get("type")),
            "name": _require_string(field.get("name")),
        }
        if "value" in field:
            field_value = field["value"]
            if field_value is not None and not isinstance(field_value, str | int | float | bool):
                raise ProviderProtocolError("Provider response is invalid")
            public_field["value"] = field_value
        public_fields.append(public_field)

    public_contact: dict[str, object] = {
        "id": contact_id,
        "project_id": expected_project_id,
        "details": public_details,
        "fields": public_fields,
    }
    for field in ("created_at", "name", "site", "profession"):
        _copy_optional_string(value, public_contact, field)
    return public_contact


def _serialize_project_field(
    value: object,
    expected_project_id: int,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Provider response is invalid")
    _validate_project_links(value, expected_project_id)
    return {
        "id": _require_identifier(value.get("id")),
        "name": _require_string(value.get("name")),
        "type": _require_string(value.get("type")),
    }


def _serialize_funnel_step(
    value: object,
    expected_project_id: int,
    *,
    expected_funnel_id: ProviderIdentifier | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Provider response is invalid")
    _validate_project_links(value, expected_project_id)
    funnel_id = _require_identifier(value.get("id"))
    if expected_funnel_id is not None and not _identifiers_match(
        funnel_id,
        expected_funnel_id,
    ):
        raise ReadAccessDenied
    return {
        "id": funnel_id,
        "name": _require_string(value.get("name")),
        "notify": _require_bool(value.get("notify")),
        "in_leads": _require_bool(value.get("in_leads")),
        "in_deals": _require_bool(value.get("in_deals")),
    }


def _serialize_label(
    value: object,
    expected_project_id: int,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Provider response is invalid")
    _validate_project_links(value, expected_project_id)
    return {
        "id": _require_identifier(value.get("id")),
        "name": _require_string(value.get("name")),
        "color": _require_string(value.get("color")),
    }


def _verified_contact(
    value: object,
    *,
    details_complete: bool,
) -> VerifiedContact:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Provider response is invalid")
    if "details" not in value:
        if details_complete:
            raise ProviderProtocolError("Provider response is incomplete")
        details: object = []
    else:
        details = value["details"]
    if not isinstance(details, list):
        raise ProviderProtocolError("Provider response is invalid")
    detail_ids: set[ProviderIdentifier] = set()
    for detail in details:
        if not isinstance(detail, dict):
            raise ProviderProtocolError("Provider response is invalid")
        detail_ids.add(_require_identifier(detail.get("id")))
    return VerifiedContact(
        contact_id=_require_identifier(value.get("id")),
        project_id=_require_int(value.get("project_id")),
        detail_ids=frozenset(detail_ids),
        details_complete=details_complete,
    )


def _serialize_view(
    value: object,
    expected_project_id: int,
    *,
    expected_view_id: str | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Provider response is invalid")
    _validate_project_links(value, expected_project_id)
    view_id = _require_int(value.get("id"))
    if _require_int(value.get("project_id")) != expected_project_id:
        raise ReadAccessDenied
    if expected_view_id is not None:
        uuid = value.get("uuid")
        matches_id = expected_view_id == str(view_id)
        matches_uuid = isinstance(uuid, str) and expected_view_id == uuid
        if not matches_id and not matches_uuid:
            raise ReadAccessDenied

    public_view: dict[str, object] = {
        "id": view_id,
        "project_id": expected_project_id,
    }
    for field in (
        "uuid",
        "ym_client_id",
        "ga_client_id",
        "source",
        "campaign",
        "keyword",
        "seo_system",
        "page",
        "referer",
    ):
        _copy_optional_string(value, public_view, field)
    return public_view


def _serialize_task(
    value: object,
    expected_project_id: int,
    *,
    expected_task_id: int | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Provider response is invalid")
    _validate_project_links(value, expected_project_id)

    task_id = _require_int(value.get("id"))
    if expected_task_id is not None and task_id != expected_task_id:
        raise ReadAccessDenied
    if _require_int(value.get("project_id")) != expected_project_id:
        raise ReadAccessDenied

    public_task: dict[str, object] = {
        "id": task_id,
        "project_id": expected_project_id,
    }
    for field in ("title", "text", "remind_method"):
        _copy_optional_string(value, public_task, field)
    for field in (
        "date_start",
        "date_end",
        "remind_type",
        "visibility",
        "status",
    ):
        _copy_optional_int(value, public_task, field)
    return public_task


def _serialize_lead(
    value: object,
    expected_project_id: int,
    *,
    expected_lead_id: int | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderProtocolError("Provider response is invalid")
    _validate_project_links(value, expected_project_id)

    lead_id = _require_int(value.get("id"))
    if expected_lead_id is not None and lead_id != expected_lead_id:
        raise ReadAccessDenied
    contact_id = _require_identifier(value.get("contact_id"))
    contact = _serialize_contact(
        value.get("contact"),
        expected_project_id,
        expected_contact_id=contact_id,
    )

    public_lead: dict[str, object] = {
        "id": lead_id,
        "contact_id": contact_id,
        "contact": contact,
    }
    for field in ("name", "created_at", "updated_at"):
        _copy_optional_string(value, public_lead, field)
    if "deal" in value:
        if type(value["deal"]) is not bool:
            raise ProviderProtocolError("Provider response is invalid")
        public_lead["deal"] = value["deal"]
    if value.get("view") is not None:
        public_lead["view"] = _serialize_view(value["view"], expected_project_id)
    return public_lead


def _object_result(response: object) -> object:
    if not isinstance(response, dict) or response.get("status") != "success":
        raise ProviderProtocolError("Provider response is invalid")
    return response.get("result")


def _list_result(response: object) -> list[object]:
    if isinstance(response, list):
        return response
    result = _object_result(response)
    if not isinstance(result, list):
        raise ProviderProtocolError("Provider response is invalid")
    return result


CatalogSerializer = Callable[[object, int], dict[str, object]]


async def _load_project_catalog(
    provider: ProviderDependency,
    path: str,
    project_id: int,
    serializer: CatalogSerializer,
) -> list[dict[str, object]]:
    response = await provider.get(path)
    _validate_project_links(response, project_id)
    result = _object_result(response)
    if not isinstance(result, list):
        raise ProviderProtocolError("Provider response is invalid")
    serialized = [serializer(item, project_id) for item in result]
    identifiers = [str(_require_identifier(item.get("id"))) for item in serialized]
    if len(identifiers) != len(set(identifiers)):
        raise ProviderProtocolError("Provider response is invalid")
    return serialized


def _require_catalog_member(
    items: list[dict[str, object]],
    expected_id: ProviderIdentifier,
) -> dict[str, object]:
    matches = [
        item
        for item in items
        if _identifiers_match(_require_identifier(item.get("id")), expected_id)
    ]
    if len(matches) != 1:
        raise ReadAccessDenied
    return matches[0]


def _ensure_provider_account(
    access: AuthenticatedSession,
    provider_account: str,
) -> None:
    if access.provider_account != provider_account:
        raise ProviderError("Provider account is unavailable")


async def verify_project_custom_id(
    custom_id: ProviderIdentifier,
    access: AuthenticatedSession,
    provider: ProviderDependency,
    provider_account: str,
) -> dict[str, object]:
    _ensure_provider_account(access, provider_account)
    items = await _load_project_catalog(
        provider,
        f"/project/{access.project_id}/customs",
        access.project_id,
        _serialize_project_field,
    )
    return _require_catalog_member(items, custom_id)


async def verify_project_field_id(
    field_id: ProviderIdentifier,
    access: AuthenticatedSession,
    provider: ProviderDependency,
    provider_account: str,
) -> dict[str, object]:
    _ensure_provider_account(access, provider_account)
    items = await _load_project_catalog(
        provider,
        f"/project/{access.project_id}/fields",
        access.project_id,
        _serialize_project_field,
    )
    return _require_catalog_member(items, field_id)


async def verify_project_funnel_id(
    funnel_id: ProviderIdentifier,
    access: AuthenticatedSession,
    provider: ProviderDependency,
    provider_account: str,
) -> dict[str, object]:
    _ensure_provider_account(access, provider_account)
    items = await _load_project_catalog(
        provider,
        f"/project/{access.project_id}/funnel",
        access.project_id,
        _serialize_funnel_step,
    )
    return _require_catalog_member(items, funnel_id)


async def verify_project_label_id(
    label_id: ProviderIdentifier,
    access: AuthenticatedSession,
    provider: ProviderDependency,
    provider_account: str,
) -> dict[str, object]:
    _ensure_provider_account(access, provider_account)
    items = await _load_project_catalog(
        provider,
        f"/label/{access.project_id}/list",
        access.project_id,
        _serialize_label,
    )
    return _require_catalog_member(items, label_id)


async def verify_contact_detail_parent(
    detail_id: ProviderIdentifier,
    access: AuthenticatedSession,
    provider: ProviderDependency,
    provider_account: str,
    ownership_store: DetailOwnershipStore,
    *,
    expected_contact_id: ProviderIdentifier | None = None,
) -> DetailOwnership:
    _ensure_provider_account(access, provider_account)
    try:
        ownership = await ownership_store.get(provider_account, detail_id)
    except DetailOwnershipConflict:
        raise ReadAccessDenied from None
    if ownership is None or not ownership.active or ownership.project_id != access.project_id:
        raise ReadAccessDenied
    if expected_contact_id is not None and not _identifiers_match(
        ownership.contact_id,
        expected_contact_id,
    ):
        raise ReadAccessDenied

    try:
        response = await provider.get(f"/contact/{ownership.contact_id}")
        raw_contact = _object_result(response)
        _serialize_contact(
            raw_contact,
            access.project_id,
            expected_contact_id=ownership.contact_id,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        await ownership_store.invalidate_if_matches(ownership)
        raise ReadAccessDenied from None
    except ProviderProtocolError:
        raise ReadAccessDenied from None

    try:
        verified_contact = _verified_contact(raw_contact, details_complete=True)
    except ProviderProtocolError:
        raise ReadAccessDenied from None
    verified_detail_ids = {str(item) for item in verified_contact.detail_ids}
    if ownership.detail_id not in verified_detail_ids:
        await ownership_store.invalidate_if_matches(ownership)
        raise ReadAccessDenied
    if not await ownership_store.is_current(ownership):
        raise ReadAccessDenied
    return ownership


@router.get("/project/{project_id}/customs", include_in_schema=False)
async def project_customs(
    project_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        result = await _load_project_catalog(
            provider,
            f"/project/{access.project_id}/customs",
            access.project_id,
            _serialize_project_field,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/project/{project_id}/fields", include_in_schema=False)
async def project_fields(
    project_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        result = await _load_project_catalog(
            provider,
            f"/project/{access.project_id}/fields",
            access.project_id,
            _serialize_project_field,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/project/{project_id}/funnel", include_in_schema=False)
async def project_funnel(
    project_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        result = await _load_project_catalog(
            provider,
            f"/project/{access.project_id}/funnel",
            access.project_id,
            _serialize_funnel_step,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/project/{project_id}/funnel/{funnel_id}", include_in_schema=False)
async def project_funnel_step(
    project_id: int,
    funnel_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    try:
        await verify_project_funnel_id(
            funnel_id,
            access,
            provider,
            provider_account,
        )
        response = await provider.get(f"/project/{access.project_id}/funnel/{funnel_id}")
        _validate_project_links(response, access.project_id)
        result = _serialize_funnel_step(
            _object_result(response),
            access.project_id,
            expected_funnel_id=funnel_id,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/label/{project_id}/list", include_in_schema=False)
async def project_labels(
    project_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        result = await _load_project_catalog(
            provider,
            f"/label/{access.project_id}/list",
            access.project_id,
            _serialize_label,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/contact/search", include_in_schema=False)
async def contacts(
    request: Request,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    ownership_store: DetailOwnershipDependency,
    project_id: int,
    phone: Annotated[list[str] | None, Query()] = None,
    email: Annotated[list[str] | None, Query()] = None,
    max_bot_id: str | None = None,
    max_user_id: str | None = None,
) -> JSONResponse:
    filters = {
        "phone": phone or [],
        "email": email or [],
        "max_bot_id": [max_bot_id] if max_bot_id is not None else [],
        "max_user_id": [max_user_id] if max_user_id is not None else [],
    }
    if not any(filters.values()):
        return request_not_valid_error()
    if any(not item or item != item.strip() for values in filters.values() for item in values):
        return request_not_valid_error()
    if any(len(request.query_params.getlist(key)) > 1 for key in ("max_bot_id", "max_user_id")):
        return request_not_valid_error()

    params: list[tuple[str, str | int]] = [("project_id", project_id)]
    for key, values in filters.items():
        params.extend((key, value) for value in values)
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get("/contact/search", params=params)
        raw_contacts = _list_result(response)
        result = [_serialize_contact(item, access.project_id) for item in raw_contacts]
        await ownership_store.sync_verified_contacts(
            access.provider_account,
            [_verified_contact(item, details_complete=False) for item in raw_contacts],
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except DetailOwnershipConflict:
        return read_not_found_error()
    except (ProviderError, DetailOwnershipUnavailable):
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/contact/{contact_id}", include_in_schema=False)
async def contact(
    contact_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    ownership_store: DetailOwnershipDependency,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get(f"/contact/{contact_id}")
        raw_contact = _object_result(response)
        result = _serialize_contact(
            raw_contact,
            access.project_id,
            expected_contact_id=contact_id,
        )
        await ownership_store.sync_verified_contacts(
            access.provider_account,
            [
                _verified_contact(
                    raw_contact,
                    details_complete=isinstance(raw_contact, dict) and "details" in raw_contact,
                )
            ],
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except DetailOwnershipConflict:
        return read_not_found_error()
    except (ProviderError, DetailOwnershipUnavailable):
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/view/{view_id}", include_in_schema=False)
async def view(
    view_id: str,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    if not view_id or view_id != view_id.strip():
        return request_not_valid_error()
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get(f"/view/{view_id}")
        result = _serialize_view(
            _object_result(response),
            access.project_id,
            expected_view_id=view_id,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/task/{task_id}", include_in_schema=False)
async def task(
    task_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get(f"/task/{task_id}")
        result = _serialize_task(
            _object_result(response),
            access.project_id,
            expected_task_id=task_id,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/task/{project_id}/list", include_in_schema=False)
async def tasks(
    project_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get(
            f"/task/{project_id}/list",
            params={"offset": offset, "limit": limit},
        )
        result = [_serialize_task(item, access.project_id) for item in _list_result(response)]
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except ProviderError:
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/lead/{lead_id}", include_in_schema=False)
async def lead(
    lead_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    ownership_store: DetailOwnershipDependency,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get(f"/lead/{lead_id}")
        raw_lead = _object_result(response)
        result = _serialize_lead(
            raw_lead,
            access.project_id,
            expected_lead_id=lead_id,
        )
        if not isinstance(raw_lead, dict):
            raise ProviderProtocolError("Provider response is invalid")
        contact_result = raw_lead.get("contact")
        await ownership_store.sync_verified_contacts(
            access.provider_account,
            [_verified_contact(contact_result, details_complete=False)],
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except DetailOwnershipConflict:
        return read_not_found_error()
    except (ProviderError, DetailOwnershipUnavailable):
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})


@router.get("/lead/{project_id}/list", include_in_schema=False)
async def leads(
    project_id: int,
    access: ProjectScopedSessionDependency,
    provider: ProviderDependency,
    provider_account: ProviderAccountDependency,
    ownership_store: DetailOwnershipDependency,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> JSONResponse:
    try:
        _ensure_provider_account(access, provider_account)
        response = await provider.get(
            f"/lead/{project_id}/list",
            params={"offset": offset, "limit": limit},
        )
        raw_leads = _list_result(response)
        result = [_serialize_lead(item, access.project_id) for item in raw_leads]
        verified_contacts: list[VerifiedContact] = []
        for item in raw_leads:
            if not isinstance(item, dict):
                raise ProviderProtocolError("Provider response is invalid")
            verified_contacts.append(_verified_contact(item.get("contact"), details_complete=False))
        await ownership_store.sync_verified_contacts(
            access.provider_account,
            verified_contacts,
        )
    except (ProviderObjectNotFound, ReadAccessDenied):
        return read_not_found_error()
    except DetailOwnershipConflict:
        return read_not_found_error()
    except (ProviderError, DetailOwnershipUnavailable):
        return provider_unavailable_error()
    return JSONResponse({"status": "success", "result": result})
