"""Manually supplied conversation sources; no import/authenticity claims."""
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, StringConstraints, field_validator
from app.contract_schemas import StrictModel

MAX_MESSAGES = 19  # One of the existing 20 finding slots is cumulative.
CUMULATIVE_WORK = "Review the cumulative effect of this ordered conversation."
ExactContent = Annotated[str, StringConstraints(min_length=3, max_length=2000)]


class ConversationMessageInput(StrictModel):
    id: UUID | None = None
    sequence: StrictInt = Field(ge=1, le=MAX_MESSAGES)
    sender: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    role: Literal["client", "freelancer", "other"]
    content: ExactContent
    sent_at: str | None = Field(default=None, min_length=10, max_length=40)

    @field_validator("content")
    @classmethod
    def nonblank_exact_text(cls, value):
        if len(value.strip()) < 3 or "\x00" in value:
            raise ValueError("Supply nonblank message text without NUL characters")
        return value  # Deliberately preserve spaces/newlines/Unicode exactly.

    @field_validator("sent_at")
    @classmethod
    def supplied_date(cls, value):
        if value is not None:
            try:
                if len(value) == 10:
                    date.fromisoformat(value)
                else:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("Use an optional ISO date/timestamp; do not invent missing dates") from None
        return value


class ConversationMessage(ConversationMessageInput):
    id: UUID


class MessageAccounting(StrictModel):
    """Server-computed accounting, independent of prose review flags."""
    message_id: UUID
    sequence: StrictInt = Field(ge=1, le=MAX_MESSAGES)
    duplicate_of_message_id: UUID | None
    counts_as_individual: bool


class MessageExcerpt(StrictModel):
    message_id: UUID
    source_excerpt: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    source_start: StrictInt | None = Field(default=None, ge=0)
    source_end: StrictInt | None = Field(default=None, ge=0)


def ordered_messages(value):
    if value is not None:
        if [item.sequence for item in value] != list(range(1, len(value) + 1)):
            raise ValueError("Messages must be supplied in contiguous sequence order starting at one")
        ids = [item.id for item in value if item.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Message identifiers must be distinct")
    return value
