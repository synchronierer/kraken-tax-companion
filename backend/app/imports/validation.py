from dataclasses import dataclass
from typing import Protocol

from app.imports.errors import ImportValidationError
from app.imports.hashing import JsonObject


class ImportValidator(Protocol):
    def validate(self, payload: JsonObject) -> None: ...


@dataclass(frozen=True)
class RequiredFieldsValidator:
    required_fields: frozenset[str] = frozenset()

    def validate(self, payload: JsonObject) -> None:
        missing = sorted(self.required_fields.difference(payload))
        if missing:
            raise ImportValidationError(
                code="missing_required_fields",
                description=f"Missing required fields: {', '.join(missing)}.",
                affected_record=payload,
            )
