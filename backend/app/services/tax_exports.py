import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.core.tax import ExportKind, TaxRuleVersion

CSV_DELIMITER = ";"
PDF_REPORT_VERSION = "tax-report-pdf-v2"


@dataclass(frozen=True, kw_only=True)
class ReportAssetRow:
    asset: str
    inflows: int
    gross_eur: Decimal
    fee_eur: Decimal
    net_eur: Decimal


@dataclass(frozen=True, kw_only=True)
class ReportInventoryRow:
    asset: str
    quantity: Decimal
    cost_eur: Decimal


@dataclass(frozen=True, kw_only=True)
class ReportReviewEvidence:
    candidates: int
    included: int
    excluded: int
    open_count: int
    included_eur: Decimal
    batch_ids: tuple[str, ...]
    versions: tuple[int, ...]
    actors: tuple[str, ...]
    decided_from: datetime | None
    decided_to: datetime | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class TaxReportData:
    run_id: str
    status: str
    period_start: date
    period_end: date
    created_at: datetime
    snapshot_hash: str
    rules_fingerprint: str
    rules: TaxRuleVersion
    gross_staking_income: Decimal
    staking_fee_included: Decimal
    reviewed_net_staking_income: Decimal
    earn_inflows: int
    disposals: int
    allocations: int
    asset_rows: tuple[ReportAssetRow, ...]
    inventory_rows: tuple[ReportInventoryRow, ...]
    review: ReportReviewEvidence


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


def _eur(value: Decimal, *, detail: bool = False) -> str:
    if detail and Decimal("0") < abs(value) < Decimal("0.000001"):
        return "< 0,000001 €"
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    integer, fraction = format(rounded, "f").split(".")
    grouped = f"{int(integer):,}".replace(",", ".")
    return f"{grouped},{fraction} €"


def _decimal(value: Decimal) -> str:
    return format(value, "f").rstrip("0").rstrip(".") or "0"


class _ReportDocTemplate(BaseDocTemplate):
    page_count: int

    def __init__(
        self, stream: io.BytesIO, *, year: int, total_pages: int | None = None
    ) -> None:
        super().__init__(
            stream,
            pagesize=A4,
            leftMargin=17 * mm,
            rightMargin=17 * mm,
            topMargin=19 * mm,
            bottomMargin=18 * mm,
            title=f"Kraken Tax Companion Steuerliche Arbeitsdokumentation {year}",
        )
        self.page_count = 0
        self.total_pages = total_pages
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height)
        self.addPageTemplates(
            PageTemplate(id="report", frames=[frame], onPage=self._footer)
        )
        self.year = year

    def afterPage(self) -> None:
        self.page_count += 1

    def _footer(self, canvas: Canvas, _: BaseDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(
            17 * mm, 10 * mm, "Kraken Tax Companion – Arbeitsdokumentation"
        )
        canvas.drawCentredString(A4[0] / 2, 10 * mm, f"Steuerjahr {self.year}")
        if self.total_pages is not None:
            canvas.drawRightString(
                A4[0] - 17 * mm,
                10 * mm,
                f"Seite {canvas.getPageNumber()} von {self.total_pages}",
            )
        canvas.restoreState()


def _table(rows: list[list[object]], widths: list[float]) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#aaaaaa")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -2),
                    [colors.white, colors.HexColor("#f3f6f8")],
                ),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def tax_report_pdf(data: TaxReportData) -> bytes:
    stream = io.BytesIO()
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            alignment=TA_CENTER,
            fontSize=20,
            leading=24,
            textColor=colors.HexColor("#17324d"),
        )
    )
    styles.add(
        ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8, leading=10)
    )
    story: list[Flowable] = []
    story.extend(
        [
            Paragraph(
                "Kraken Tax Companion<br/>Steuerliche Arbeitsdokumentation "
                f"{data.period_start.year}",
                styles["ReportTitle"],
            ),
            Spacer(1, 8 * mm),
            Paragraph(
                f"<b>Zeitraum:</b> {data.period_start:%d.%m.%Y} – "
                f"{data.period_end:%d.%m.%Y}",
                styles["BodyText"],
            ),
            Paragraph(f"<b>TaxRun-ID:</b> {data.run_id}", styles["BodyText"]),
            Paragraph(
                f"<b>TaxRun-Status:</b> {data.status.upper()}", styles["BodyText"]
            ),
            Paragraph(
                f"<b>Erstellt:</b> {data.created_at:%d.%m.%Y %H:%M:%S} UTC",
                styles["BodyText"],
            ),
            Paragraph(
                f"<b>Berichtsversion:</b> {PDF_REPORT_VERSION}", styles["BodyText"]
            ),
            Spacer(1, 8 * mm),
            _table(
                [
                    ["Kennzahl", "Wert"],
                    ["Brutto-Staking-Erträge", _eur(data.gross_staking_income)],
                    ["Plattformgebühren", _eur(data.staking_fee_included)],
                    [
                        "Geprüfter Netto-Arbeitswert",
                        _eur(data.reviewed_net_staking_income),
                    ],
                ],
                [110 * mm, 55 * mm],
            ),
            Spacer(1, 6 * mm),
            Paragraph(
                f"{data.earn_inflows} Earn-Zuflüsse · "
                f"{data.review.included + data.review.excluded} manuell geprüfte "
                f"Plattformgebühren · {data.review.open_count} offene "
                f"Prüfentscheidungen · {data.disposals} Veräußerungen · "
                f"{data.allocations} FIFO-Zuordnungen",
                styles["BodyText"],
            ),
            Spacer(1, 5 * mm),
            Paragraph(
                "Anzeigewerte sind auf Cent gerundet. Die Berechnung und die "
                "maschinenlesbaren Nachweise verwenden die exakten Decimalwerte.",
                styles["Small"],
            ),
            PageBreak(),
            Paragraph("Staking-Übersicht", styles["Heading1"]),
        ]
    )
    asset_rows: list[list[object]] = [
        ["Asset", "Zuflüsse", "Brutto EUR", "Plattformgebühr EUR", "Netto EUR"]
    ]
    asset_rows.extend(
        [
            [
                row.asset,
                row.inflows,
                _eur(row.gross_eur, detail=True),
                _eur(row.fee_eur, detail=True),
                _eur(row.net_eur, detail=True),
            ]
            for row in data.asset_rows
        ]
    )
    asset_rows.append(
        [
            "Gesamt",
            data.earn_inflows,
            _eur(data.gross_staking_income),
            _eur(data.staking_fee_included),
            _eur(data.reviewed_net_staking_income),
        ]
    )
    story.extend(
        [
            _table(asset_rows, [24 * mm, 20 * mm, 40 * mm, 48 * mm, 38 * mm]),
            Spacer(1, 8 * mm),
            Paragraph("Bestandsübersicht", styles["Heading2"]),
        ]
    )
    inventory_rows: list[list[object]] = [
        ["Asset", "Verbleibende Menge", "Einstandswert EUR"]
    ]
    inventory_rows.extend(
        [
            [row.asset, _decimal(row.quantity), _eur(row.cost_eur, detail=True)]
            for row in data.inventory_rows
        ]
    )
    story.extend(
        [
            _table(inventory_rows, [40 * mm, 65 * mm, 60 * mm]),
            Spacer(1, 5 * mm),
            Paragraph(
                "Die vollständigen Einzellose sind im CSV- und API-Nachweis enthalten.",
                styles["Small"],
            ),
            PageBreak(),
            Paragraph("Prüfung der Staking-Plattformgebühren", styles["Heading1"]),
        ]
    )
    review = data.review
    decision_rows: list[list[object]] = [
        ["Gebührenkandidaten", str(review.candidates)],
        ["Als Werbungskosten berücksichtigt", str(review.included)],
        ["Nicht berücksichtigt", str(review.excluded)],
        ["Offen", str(review.open_count)],
        ["Berücksichtigter Gesamtbetrag", _eur(review.included_eur)],
        ["Decision-Batches", str(len(review.batch_ids))],
        ["Batch-ID", ", ".join(review.batch_ids) or "—"],
        ["Decision-Version", ", ".join(map(str, review.versions)) or "—"],
        ["Akteur", ", ".join(review.actors) or "—"],
    ]
    decision_table_rows: list[list[object]] = [
        ["Manuelle steuerliche Arbeitsentscheidung", "Nachweis"]
    ]
    decision_table_rows.extend(decision_rows)
    story.extend(
        [
            _table(decision_table_rows, [78 * mm, 87 * mm]),
            Spacer(1, 4 * mm),
        ]
    )
    if review.decided_from and review.decided_to:
        story.append(
            Paragraph(
                f"<b>Entscheidungszeitraum:</b> "
                f"{review.decided_from:%d.%m.%Y %H:%M:%S} UTC – "
                f"{review.decided_to:%d.%m.%Y %H:%M:%S} UTC",
                styles["Small"],
            )
        )
    for reason in review.reasons:
        story.append(
            KeepTogether(
                [
                    Paragraph("<b>Gemeinsame Begründung:</b>", styles["BodyText"]),
                    Paragraph(escape(reason), styles["BodyText"]),
                ]
            )
        )
    story.extend(
        [
            Spacer(1, 5 * mm),
            Paragraph("Methodik", styles["Heading2"]),
            Paragraph(
                f"<b>TaxRun-ID:</b> {data.run_id}<br/>"
                f"<b>Snapshot-Hash:</b> {data.snapshot_hash}<br/>"
                f"<b>Rule-Fingerprint:</b> {data.rules_fingerprint}<br/>"
                f"<b>FIFO-Regel:</b> {data.rules.fifo}<br/>"
                f"<b>Gebührenregel:</b> {data.rules.fees}<br/>"
                f"<b>Klassifikationsregel:</b> {data.rules.classification}<br/>"
                f"<b>Journalregel:</b> {data.rules.journal}<br/>"
                f"<b>TaxRun-Exportregel:</b> {data.rules.export}<br/>"
                f"<b>PDF-Berichtsversion:</b> {PDF_REPORT_VERSION}",
                styles["Small"],
            ),
            Spacer(1, 4 * mm),
            Paragraph("FIFO / Veräußerungen", styles["Heading2"]),
            Paragraph(
                (
                    "Im Berichtszeitraum liegen keine erfassten Veräußerungen vor. "
                    "Daher waren für diesen Steuerlauf keine FIFO-Zuordnungen "
                    "erforderlich."
                    if data.disposals == 0
                    else f"Im Berichtszeitraum wurden {data.disposals} Veräußerungen "
                    f"und {data.allocations} FIFO-Zuordnungen nachgewiesen."
                ),
                styles["BodyText"],
            ),
            Spacer(1, 4 * mm),
            Paragraph("Exakte Rechenwerte", styles["Heading2"]),
            Paragraph(
                f"Brutto: {_decimal(data.gross_staking_income)} EUR<br/>"
                f"Plattformgebühren: {_decimal(data.staking_fee_included)} EUR<br/>"
                f"Netto: {_decimal(data.reviewed_net_staking_income)} EUR",
                styles["Small"],
            ),
            Spacer(1, 6 * mm),
            Paragraph(
                "Dieser Bericht ist eine Arbeitsdokumentation und keine "
                "Steuerberatung.",
                styles["BodyText"],
            ),
        ]
    )
    counting_doc = _ReportDocTemplate(io.BytesIO(), year=data.period_start.year)
    counting_doc.build(deepcopy(story))
    doc = _ReportDocTemplate(
        stream,
        year=data.period_start.year,
        total_pages=counting_doc.page_count,
    )
    doc.build(story)
    return stream.getvalue()


def export_extension(kind: ExportKind) -> tuple[str, str]:
    if kind == ExportKind.TAX_REPORT_PDF:
        return "pdf", "application/pdf"
    return "csv", "text/csv; charset=utf-8"
