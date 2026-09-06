from dataclasses import dataclass

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import FromClause

from app.core.entities import ImportSession
from app.core.valuation import ValuationDecisionStatus, ValuationRun
from app.database.mappings import (
    acquisition_lots,
    audit_events,
    disposal_events,
    financial_review_record_links,
    import_sessions,
    raw_import_records,
    tax_review_cases,
    trade_executions,
    transformation_issues,
    transformation_runs,
    valuation_decisions,
    valuation_requirements,
    valuation_runs,
)


@dataclass(frozen=True)
class DashboardCounts:
    imports: int
    raw_records: int
    transformation_runs: int
    acquisitions: int
    trades: int
    disposals: int
    valuation_requirements: int
    valuation_decisions: int
    resolved_valuation_decisions: int
    review_cases: int


class SqlAlchemyDashboardQueries:
    def __init__(self, session: Session) -> None:
        self._session = session

    def counts(self) -> DashboardCounts:
        successor = valuation_decisions.alias("successor")
        is_current = ~exists(
            select(successor.c.id).where(
                successor.c.supersedes_id == valuation_decisions.c.id
            )
        )
        resolved_financial_issue = exists(
            select(financial_review_record_links.c.id).where(
                financial_review_record_links.c.raw_import_record_id
                == transformation_issues.c.raw_import_record_id,
                financial_review_record_links.c.resolution_id.is_not(None),
            )
        )
        open_transformation_issues = int(
            self._session.scalar(
                select(func.count())
                .select_from(transformation_issues)
                .where(~resolved_financial_issue)
            )
            or 0
        )
        return DashboardCounts(
            imports=self._count(import_sessions),
            raw_records=self._count(raw_import_records),
            transformation_runs=self._count(transformation_runs),
            acquisitions=self._count(acquisition_lots),
            trades=self._count(trade_executions),
            disposals=self._count(disposal_events),
            valuation_requirements=self._count(valuation_requirements),
            valuation_decisions=int(
                self._session.scalar(
                    select(func.count())
                    .select_from(valuation_decisions)
                    .where(is_current)
                )
                or 0
            ),
            resolved_valuation_decisions=int(
                self._session.scalar(
                    select(func.count())
                    .select_from(valuation_decisions)
                    .where(is_current)
                    .where(
                        valuation_decisions.c.status == ValuationDecisionStatus.RESOLVED
                    )
                )
                or 0
            ),
            review_cases=int(
                self._session.scalar(
                    select(func.count())
                    .select_from(valuation_decisions)
                    .where(
                        valuation_decisions.c.status
                        == ValuationDecisionStatus.REVIEW_REQUIRED
                    )
                    .where(is_current)
                )
                or 0
            )
            + open_transformation_issues
            + self._count(tax_review_cases)
            + int(
                self._session.scalar(
                    select(func.count())
                    .select_from(audit_events)
                    .where(
                        audit_events.c.event_type == "valuation.provider_fetch_failed"
                    )
                )
                or 0
            ),
        )

    def latest_import(self) -> ImportSession | None:
        return self._session.scalars(
            select(ImportSession).order_by(import_sessions.c.started_at.desc()).limit(1)
        ).first()

    def latest_valuation_run(self) -> ValuationRun | None:
        return self._session.scalars(
            select(ValuationRun).order_by(valuation_runs.c.started_at.desc()).limit(1)
        ).first()

    def _count(self, table: FromClause) -> int:
        return int(self._session.scalar(select(func.count()).select_from(table)) or 0)
