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

type ExactDecimal = { coefficient: bigint; decimalExponent: bigint };

function parseExactDecimal(value: string): ExactDecimal {
  const match = /^(?<sign>-?)(?<integer>\d+)(?:\.(?<fraction>\d+))?(?:[eE](?<exponent>[+-]?\d+))?$/.exec(value);
  if (!match?.groups) throw new Error("Ungültiger Decimal-String.");
  const fraction = match.groups.fraction ?? "";
  const explicitExponent = BigInt(match.groups.exponent ?? "0");
  const unsignedCoefficient = BigInt(`${match.groups.integer}${fraction}`);
  return {
    coefficient: match.groups.sign === "-" ? -unsignedCoefficient : unsignedCoefficient,
    decimalExponent: explicitExponent - BigInt(fraction.length),
  };
}

function decimalZeros(count: bigint): string {
  let result = "";
  for (let remaining = count; remaining > 0n; remaining -= 1n) result += "0";
  return result;
}

function plainDecimal(coefficient: bigint, decimalExponent: bigint): string {
  if (coefficient === 0n) return "0";
  let normalized = coefficient;
  let exponent = decimalExponent;
  while (normalized % 10n === 0n) {
    normalized /= 10n;
    exponent += 1n;
  }
  const negative = normalized < 0n;
  const digits = (negative ? -normalized : normalized).toString();
  const sign = negative ? "-" : "";
  if (exponent >= 0n) return `${sign}${digits}${decimalZeros(exponent)}`;
  const scale = -exponent;
  if (scale >= BigInt(digits.length)) {
    return `${sign}0.${decimalZeros(scale - BigInt(digits.length))}${digits}`;
  }
  let integer = "";
  let fraction = "";
  let remaining = BigInt(digits.length);
  for (const digit of digits) {
    if (remaining > scale) integer += digit;
    else fraction += digit;
    remaining -= 1n;
  }
  return `${sign}${integer}.${fraction}`;
}

export function sumDecimalStrings(values: string[]): string {
  if (values.length === 0) return "0";
  const parsed = values.map(parseExactDecimal);
  const commonExponent = parsed.reduce(
    (smallest, value) => value.decimalExponent < smallest ? value.decimalExponent : smallest,
    parsed[0].decimalExponent,
  );
  const total = parsed.reduce(
    (result, value) => result + value.coefficient * 10n ** (value.decimalExponent - commonExponent),
    0n,
  );
  return plainDecimal(total, commonExponent);
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
