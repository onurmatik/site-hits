from datetime import datetime
from decimal import Decimal
from typing import Literal, TypeAlias

from ninja import Field, Schema
from pydantic import field_validator


JsonScalar: TypeAlias = str | int | float | bool | None


class ViewportSchema(Schema):
    width: int = Field(ge=0, le=10000)
    height: int = Field(ge=0, le=10000)


class AutomationSignalsSchema(Schema):
    webdriver: bool = False


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
    automation: AutomationSignalsSchema = Field(default_factory=AutomationSignalsSchema)
    properties: dict[str, JsonScalar] = Field(default_factory=dict)
    actor_token: str = Field(default="", max_length=2048)
    value: Decimal | None = None
    unit: str = Field(default="", max_length=32)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value):
        if value not in {"pageview", "custom"}:
            raise ValueError("event_type must be pageview or custom")
        return value


class BotEventPayload(Schema):
    url: str = Field(max_length=4096)
    user_agent: str = Field(min_length=1, max_length=1024)
    status_code: int | None = Field(default=None, ge=100, le=599)
    timestamp: datetime | None = None


class ServerEventPayload(Schema):
    event_id: str = Field(min_length=1, max_length=255)
    event_name: str = Field(min_length=1, max_length=64)
    actor_id: str = Field(default="", max_length=255)
    timestamp: datetime | None = None
    value: Decimal | None = None
    unit: str = Field(default="", max_length=32)
    path: str = Field(default="", max_length=2048)
    properties: dict[str, JsonScalar] = Field(default_factory=dict)


class ForgetActorPayload(Schema):
    actor_id: str = Field(min_length=1, max_length=255)


class AcceptedResponse(Schema):
    accepted: bool


class BotAcceptedResponse(AcceptedResponse):
    classification: Literal["known_crawler", "unrecognized"]


class ServerAcceptedResponse(AcceptedResponse):
    duplicate: bool


class ForgetActorResponse(Schema):
    deleted_events: int


class ErrorDetail(Schema):
    message: str


class ErrorResponse(Schema):
    error: ErrorDetail
