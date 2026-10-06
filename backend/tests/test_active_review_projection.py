from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.transformation import (
    DecisionType,
    TransformationDecision,
    TransformationIssue,
    TransformationRun,
    TransformationStatus,
    active_transformation_issues,
)


def _run(started_at: datetime) -> TransformationRun:
    return TransformationRun(
        contract_version="test-v1",
        status=TransformationStatus.COMPLETED_WITH_REVIEW,
        started_at=started_at,
        completed_at=started_at,
        actor_id="test",
        checked_records=1,
        review_cases=1,
    )


def _issue(
    run: TransformationRun,
    raw_id,
    code: str,
    occurred_at: datetime,
):
    return TransformationIssue(
        transformation_run_id=run.id,
        raw_import_record_id=raw_id,
        code=code,
        message="requires review",
        is_conflict=False,
        occurred_at=occurred_at,
    )


def test_active_projection_deduplicates_three_records_across_two_runs() -> None:
    first_at = datetime(2026, 1, 1, tzinfo=UTC)
    first = _run(first_at)
    second = _run(first_at + timedelta(minutes=1))
    records = [uuid4() for _ in range(3)]
    issues = [
        _issue(first, raw_id, "ledger_deposit_requires_review", first_at)
        for raw_id in records
    ] + [
        _issue(second, raw_id, "ledger_deposit_requires_review", second.started_at)
        for raw_id in records
    ]

    projected = active_transformation_issues(
        issues, {first.id: first, second.id: second}
    )

    assert len(issues) == 6
    assert len(projected) == 3
    assert {item.raw_import_record_id for item in projected} == set(records)
    assert {item.transformation_run_id for item in projected} == {second.id}


def test_active_projection_identity_includes_issue_code_and_is_deterministic() -> None:
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    run = _run(started_at)
    raw_id = uuid4()
    first = _issue(run, raw_id, "code_a", started_at)
    second = _issue(run, raw_id, "code_a", started_at)
    third = _issue(run, raw_id, "code_b", started_at)

    projected = active_transformation_issues([third, second, first], {run.id: run})

    assert len(projected) == 2
    assert {item.code for item in projected} == {"code_a", "code_b"}
    selected = next(item for item in projected if item.code == "code_a")
    assert selected.id == max(first.id, second.id, key=str)


def _decision(
    run: TransformationRun,
    raw_id,
    decision_type: DecisionType,
    reason_code: str,
    decided_at: datetime,
) -> TransformationDecision:
    return TransformationDecision(
        raw_import_record_id=raw_id,
        import_session_id=uuid4(),
        transformation_run_id=run.id,
        contract_version="test-v1",
        decision_type=decision_type,
        reason_code=reason_code,
        explanation="test decision",
        decided_at=decided_at,
    )


def test_non_reviewing_latest_decision_supersedes_historical_issue() -> None:
    first_at = datetime(2026, 1, 1, tzinfo=UTC)
    first = _run(first_at)
    second = _run(first_at + timedelta(minutes=1))
    raw_id = uuid4()
    issue = _issue(first, raw_id, "ledger_deposit_requires_review", first_at)
    decision = _decision(
        second,
        raw_id,
        DecisionType.INTERNAL_MOVEMENT,
        "ledger_historical_self_transfer_resolved",
        second.started_at,
    )

    assert (
        active_transformation_issues(
            [issue], {first.id: first, second.id: second}, [decision]
        )
        == ()
    )


def test_latest_review_decision_keeps_only_matching_current_issue() -> None:
    first_at = datetime(2026, 1, 1, tzinfo=UTC)
    first = _run(first_at)
    second = _run(first_at + timedelta(minutes=1))
    raw_id = uuid4()
    old = _issue(first, raw_id, "old_review", first_at)
    current = _issue(second, raw_id, "current_review", second.started_at)
    old_decision = _decision(
        first, raw_id, DecisionType.REVIEW_REQUIRED, "old_review", first.started_at
    )
    decision = _decision(
        second,
        raw_id,
        DecisionType.REVIEW_REQUIRED,
        "current_review",
        second.started_at,
    )

    projected = active_transformation_issues(
        [old, current],
        {first.id: first, second.id: second},
        [old_decision, decision],
    )

    assert [item.code for item in projected] == ["current_review"]


def test_latest_conflict_decision_keeps_matching_issue() -> None:
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    run = _run(started_at)
    raw_id = uuid4()
    issue = _issue(run, raw_id, "asset_conflict", started_at)
    decision = _decision(
        run, raw_id, DecisionType.CONFLICT, "asset_conflict", started_at
    )

    projected = active_transformation_issues([issue], {run.id: run}, [decision])

    assert projected == (issue,)


def test_missing_decision_run_fails_safe_and_keeps_issue() -> None:
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    run = _run(started_at)
    missing_run_id = uuid4()
    raw_id = uuid4()
    issue = _issue(run, raw_id, "ledger_deposit_requires_review", started_at)
    decision = TransformationDecision(
        raw_import_record_id=raw_id,
        import_session_id=uuid4(),
        transformation_run_id=missing_run_id,
        contract_version="test-v1",
        decision_type=DecisionType.INTERNAL_MOVEMENT,
        reason_code="ledger_historical_self_transfer_resolved",
        explanation="orphaned decision",
        decided_at=started_at + timedelta(minutes=1),
    )

    assert active_transformation_issues([issue], {run.id: run}, [decision]) == (issue,)


def test_cost_basis_gap_remains_active_after_transfer_decision() -> None:
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    run = _run(started_at)
    raw_id = uuid4()
    issue = _issue(run, raw_id, "historical_cost_basis_gap", started_at)
    decision = _decision(
        run,
        raw_id,
        DecisionType.INTERNAL_MOVEMENT,
        "ledger_historical_self_transfer_resolved",
        started_at,
    )

    assert active_transformation_issues([issue], {run.id: run}, [decision]) == (issue,)
