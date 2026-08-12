import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.core.tax import ExportKind, TaxRuleVersion

CSV_DELIMITER = ";"


def csv_bytes(columns: Sequence[str], rows: Iterable[Mapping[str, object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=list(columns),
        delimiter=CSV_DELIMITER,
        lineterminator="\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _csv_value(row.get(column)) for column in columns})
    return stream.getvalue().encode("utf-8")


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def safe_export_path(export_directory: Path, file_name: str) -> Path:
    if Path(file_name).name != file_name or file_name in {"", ".", ".."}:
        raise ValueError("Export file name must be a safe base name.")
    root = export_directory.resolve()
    target = (root / file_name).resolve()
    if target.parent != root:
        raise ValueError("Export path escapes the configured directory.")
    return target


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def tax_report_pdf(
    *,
    period_start: date,
    period_end: date,
    created_at: datetime,
    rules: TaxRuleVersion,
    summary: dict[str, Any],
) -> bytes:
    lines = [
        "Kraken Tax Companion - Steuerliche Arbeitsdokumentation",
        f"Zeitraum: {period_start.isoformat()} bis {period_end.isoformat()}",
        f"Erstellt: {created_at.isoformat()}",
        f"FIFO-Regel: {rules.fifo}",
        f"Gebuehrenregel: {rules.fees}",
        f"Klassifikation: {rules.classification}",
        "Zusammenfassung",
        f"Realisierte Gewinne: {summary.get('realized_gains', '0')} EUR",
        f"Realisierte Verluste: {summary.get('realized_losses', '0')} EUR",
        f"Saldiertes Ergebnis: {summary.get('net_result', '0')} EUR",
        f"Anzahl Earn-Zufluesse: {summary.get('earn_inflows', '0')}",
        f"Gebuehren: {summary.get('fees', '0')} EUR",
        "Staking-Gebuehrenkandidaten: "
        f"{summary.get('staking_fee_candidates', '0')} EUR",
        f"Manuell beruecksichtigt: {summary.get('staking_fee_included', '0')} EUR",
        "Manuell nicht beruecksichtigt: "
        f"{summary.get('staking_fee_excluded', '0')} EUR",
        f"Noch offen: {summary.get('staking_fee_open', '0')} EUR",
        "Gepruefter Netto-Arbeitswert: "
        f"{summary.get('reviewed_net_staking_income', '0')} EUR",
        "Gewinne und Verluste",
        "Earn-Zufluesse",
        "Gebuehren",
        "Offene Prueffaelle",
        "Bestandsuebersicht",
        "FIFO-Nachweis",
        "Methoden- und Quellenhinweise",
        "Dieser Bericht ist eine Arbeitsdokumentation und keine Steuerberatung.",
    ]
    commands = ["BT", "/F1 10 Tf", "50 790 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -18 Td")
        commands.append(f"({_pdf_escape(str(line))}) Tj")
    commands.append("ET")
    content = "\n".join(commands).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(content)).encode()
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode())
        result.extend(obj)
        result.extend(b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(result)


def export_extension(kind: ExportKind) -> tuple[str, str]:
    if kind == ExportKind.TAX_REPORT_PDF:
        return "pdf", "application/pdf"
    return "csv", "text/csv; charset=utf-8"
