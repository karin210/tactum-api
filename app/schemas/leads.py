import uuid

from pydantic import Field

from app.schemas.base import ApiModel, Email, OptionalString, RequestModel


class LeadIn(RequestModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: OptionalString = Field(default=None, max_length=100)
    email: Email
    phone: OptionalString = Field(default=None, pattern=r"^\+?[0-9 ()-]{7,30}$")
    city: OptionalString = Field(default=None, max_length=100)


class LeadOut(ApiModel):
    id: uuid.UUID
    email: str
    user_type: str
