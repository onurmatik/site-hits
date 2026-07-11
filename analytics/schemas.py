from datetime import datetime
from typing import TypeAlias

from ninja import Field, Schema
from pydantic import field_validator


JsonScalar: TypeAlias = str | int | float | bool | None


class ViewportSchema(Schema):
    width: int = Field(ge=0, le=10000)
    height: int = Field(ge=0, le=10000)


class EventPayload(Schema):
    site_key: str = Field(min_length=8, max_length=64)
    event_type: str
    event_name: str = Field(default="", max_length=64)
    session_id: str = Field(min_length=8, max_length=64)
    url: str = Field(max_length=4096)
    referrer: str = Field(default="", max_length=4096)
    timestamp: datetime
    language: str = Field(default="", max_length=35)
    timezone: str = Field(default="", max_length=64)
    viewport: ViewportSchema
    screen: ViewportSchema
    properties: dict[str, JsonScalar] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value):
        if value not in {"pageview", "custom"}:
            raise ValueError("event_type must be pageview or custom")
        return value


class AcceptedResponse(Schema):
    accepted: bool


class ErrorDetail(Schema):
    message: str


class ErrorResponse(Schema):
    error: ErrorDetail

