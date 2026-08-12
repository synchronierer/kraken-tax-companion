export type DisplayValue = string | number | boolean | null | undefined;

export function displayValue(value: DisplayValue): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "ja" : "nein";
  }
  return String(value);
}

const eurFormatter = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const quantityFormatter = new Intl.NumberFormat("de-DE", {
  useGrouping: false,
  maximumFractionDigits: 8,
});

export function formatEurDecimal(value: string | number): string {
  return eurFormatter.format(Number(value));
}

export function formatAssetQuantity(value: string): string {
  return quantityFormatter.format(Number(value));
}

export function sumDecimalStrings(values: string[]): string {
  const parsed = values.map((value) => {
    const match = /^(?<sign>-?)(?<integer>\d+)(?:\.(?<fraction>\d+))?$/.exec(value);
    if (!match?.groups) throw new Error("Ungültiger Decimal-String.");
    return {
      negative: match.groups.sign === "-",
      integer: match.groups.integer,
      fraction: match.groups.fraction ?? "",
    };
  });
  const scale = Math.max(0, ...parsed.map((value) => value.fraction.length));
  const total = parsed.reduce((result, value) => {
    const digits = `${value.integer}${value.fraction.padEnd(scale, "0")}`;
    return result + BigInt(`${value.negative ? "-" : ""}${digits}`);
  }, 0n);
  const negative = total < 0n;
  const absolute = (negative ? -total : total).toString().padStart(scale + 1, "0");
  if (scale === 0) return `${negative ? "-" : ""}${absolute}`;
  const integer = absolute.slice(0, -scale);
  const fraction = absolute.slice(-scale).replace(/0+$/, "");
  return `${negative ? "-" : ""}${integer}${fraction ? `.${fraction}` : ""}`;
}

export function reviewDecisionLabel(decision: string): string {
  return decision === "include_as_werbungskosten"
    ? "als Werbungskosten berücksichtigen"
    : "nicht als Werbungskosten berücksichtigen";
}

export function reviewSubmissionAction(
  selectedCount: number,
  decision: string,
  reason: string,
): "invalid" | "confirm" | "submit" {
  if (selectedCount < 1 || !decision || !reason.trim()) return "invalid";
  return selectedCount > 1 ? "confirm" : "submit";
}

export function isEmptyDashboard(values: {
  imports: number;
  raw_records: number;
  rewards: number;
  trades: number;
  resolved_valuations: number;
  open_valuations: number;
  review_cases: number;
}): boolean {
  return Object.values(values).every((value) => value === 0);
}

export type ManualPriceFields = {
  asset: string;
  date: string;
  price_eur: string;
  source: string;
  reason: string;
};

export function validateManualPrice(fields: ManualPriceFields): string | null {
  if (
    !fields.asset.trim() ||
    !fields.date ||
    !fields.source.trim() ||
    !fields.reason.trim()
  ) {
    return "Asset, Datum, Quelle und Begründung sind Pflichtfelder.";
  }
  if (!/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(fields.price_eur)) {
    return "Der Kurs muss eine positive Dezimalzahl mit Punkt als Trennzeichen sein.";
  }
  if (/^0(?:\.0+)?$/.test(fields.price_eur)) {
    return "Der Kurs muss größer als null sein.";
  }
  return null;
}

export function reviewGroup(code: string): string {
  if (code.includes("mapping") || code.includes("asset")) return "Unbekanntes Asset";
  if (code.includes("pair")) return "Unbekanntes Paar";
  if (code.includes("coverage")) return "Unvollständige Kursdaten";
  if (code.includes("provider")) return "Providerfehler";
  if (code.includes("conflict")) return "Konflikt";
  if (code.includes("manual")) return "Manuelle Bewertung erforderlich";
  return "Sonstiger Prüffall";
}

export function safeSystemEntries(
  values: Record<string, DisplayValue>,
): [string, DisplayValue][] {
  return Object.entries(values).filter(
    ([key]) => !/api[_-]?key$|secret|token|password/i.test(key),
  );
}

export function pageQuery(offset: number, limit: number): string {
  const safeOffset = Math.max(0, Math.trunc(offset));
  const safeLimit = Math.min(200, Math.max(1, Math.trunc(limit)));
  return `offset=${safeOffset}&limit=${safeLimit}`;
}
