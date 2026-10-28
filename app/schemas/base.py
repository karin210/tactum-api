from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """Base for request/response bodies: snake_case in Python, camelCase in JSON."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        str_strip_whitespace=True,
    )


class RequestModel(ApiModel):
    """Request bodies reject fields they don't know about instead of silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


def _blank_to_none(value: Any) -> Any:
    """HTML forms send "" for empty optional inputs; treat that as not provided."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _normalize_email(value: Any) -> Any:
    value = _blank_to_none(value)
    return value.strip().lower() if isinstance(value, str) else value


# Reusable field types for request bodies. Emails are trimmed and lowercased before validation.
Email = Annotated[EmailStr, BeforeValidator(_normalize_email), Field(max_length=254)]
OptionalEmail = Annotated[EmailStr | None, BeforeValidator(_normalize_email)]
OptionalString = Annotated[str | None, BeforeValidator(_blank_to_none)]
