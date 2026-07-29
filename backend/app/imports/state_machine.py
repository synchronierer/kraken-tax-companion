from datetime import datetime

from app.core.entities import ImportSession, ImportStatus
from app.core.time import require_utc

FAILURE_TARGETS = {ImportStatus.FAILED, ImportStatus.CANCELLED}
ALLOWED_TRANSITIONS: dict[ImportStatus, frozenset[ImportStatus]] = {
    ImportStatus.CREATED: frozenset({ImportStatus.RECEIVED, *FAILURE_TARGETS}),
    ImportStatus.RECEIVED: frozenset({ImportStatus.VALIDATING, *FAILURE_TARGETS}),
    ImportStatus.VALIDATING: frozenset({ImportStatus.HASHING, *FAILURE_TARGETS}),
    ImportStatus.HASHING: frozenset(
        {ImportStatus.CHECKING_DUPLICATES, *FAILURE_TARGETS}
    ),
    ImportStatus.CHECKING_DUPLICATES: frozenset(
        {ImportStatus.PERSISTING, ImportStatus.COMPLETED, *FAILURE_TARGETS}
    ),
    ImportStatus.PERSISTING: frozenset({ImportStatus.COMPLETED, *FAILURE_TARGETS}),
    ImportStatus.COMPLETED: frozenset(),
    ImportStatus.FAILED: frozenset(),
    ImportStatus.CANCELLED: frozenset(),
}


class InvalidImportTransition(ValueError):
    pass


def transition(
    session: ImportSession, target: ImportStatus, occurred_at: datetime
) -> None:
    occurred_at = require_utc(occurred_at)
    if target not in ALLOWED_TRANSITIONS[session.status]:
        raise InvalidImportTransition(
            f"Transition from {session.status.value} to {target.value} is not allowed."
        )
    session.status = target
    session.updated_at = occurred_at
    if target in {ImportStatus.COMPLETED, *FAILURE_TARGETS}:
        session.ended_at = occurred_at
