from datetime import datetime

from app.core.time import require_utc
from app.core.transformation import TransformationRun, TransformationStatus

ALLOWED_TRANSFORMATION_TRANSITIONS = {
    TransformationStatus.CREATED: frozenset(
        {TransformationStatus.PROCESSING, TransformationStatus.FAILED}
    ),
    TransformationStatus.PROCESSING: frozenset(
        {
            TransformationStatus.COMPLETED,
            TransformationStatus.COMPLETED_WITH_REVIEW,
            TransformationStatus.FAILED,
        }
    ),
    TransformationStatus.COMPLETED: frozenset(),
    TransformationStatus.COMPLETED_WITH_REVIEW: frozenset(),
    TransformationStatus.FAILED: frozenset(),
}


def transition_transformation(
    run: TransformationRun, target: TransformationStatus, occurred_at: datetime
) -> None:
    occurred_at = require_utc(occurred_at)
    if target not in ALLOWED_TRANSFORMATION_TRANSITIONS[run.status]:
        raise ValueError(
            f"Transformation transition from {run.status.value} "
            f"to {target.value} is not allowed."
        )
    run.status = target
    if target in {
        TransformationStatus.COMPLETED,
        TransformationStatus.COMPLETED_WITH_REVIEW,
        TransformationStatus.FAILED,
    }:
        run.completed_at = occurred_at
