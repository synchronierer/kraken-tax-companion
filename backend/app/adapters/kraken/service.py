from collections.abc import Callable
from datetime import datetime

from app.adapters.kraken.models import KrakenCsvImportResult
from app.adapters.kraken.parser import KrakenCsvParser
from app.core.entities import AuditActorType, ImportSession, ImportStatus
from app.core.identifiers import IdGenerator
from app.core.time import utc_now
from app.imports.context import ImportContext
from app.imports.service import ImportService


class KrakenCsvImportService:
    def __init__(
        self,
        *,
        import_service: ImportService,
        id_generator: IdGenerator,
        parser: KrakenCsvParser | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._import_service = import_service
        self._id_generator = id_generator
        self._parser = parser or KrakenCsvParser()
        self._clock = clock

    def import_csv(
        self,
        *,
        raw_data: bytes | str,
        actor_type: AuditActorType,
        actor_id: str,
        source_name: str | None = None,
    ) -> KrakenCsvImportResult:
        batch, errors = self._parser.parse(raw_data)
        if batch is None:
            return KrakenCsvImportResult(batch=None, import_result=None, errors=errors)
        received_at = self._clock()
        kind = batch.export_kind
        session = ImportSession(
            source=kind.source,
            version=kind.contract_version,
            status=ImportStatus.CREATED,
            started_at=received_at,
            correlation_id=self._id_generator.new(),
            actor_type=actor_type,
            actor_id=actor_id,
        )
        context = ImportContext(
            session=session,
            source=kind.source,
            version=kind.contract_version,
            received_at=received_at,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=session.correlation_id,
            source_name=source_name or kind.value,
            metadata={"export_kind": kind.value},
        )
        result = self._import_service.import_records(
            context=context, records=batch.raw_records()
        )
        return KrakenCsvImportResult(
            batch=batch, import_result=result, errors=result.errors
        )
