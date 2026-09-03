/// <reference types="vite/client" />

const configuredBaseUrl: unknown = import.meta.env.VITE_API_BASE_URL;
const baseUrl =
  typeof configuredBaseUrl === "string"
    ? configuredBaseUrl.trim().replace(/\/+$/, "")
    : "";

export function apiUrl(path: string): string {
  const normalizedPath = `/${path.replace(/^\/+/, "")}`;
  return `${baseUrl}${normalizedPath}`;
}

export type Page<T> = { items: T[]; total: number; offset: number; limit: number };
export type Dashboard = {
  imports: number; raw_records: number; rewards: number; trades: number;
  resolved_valuations: number; open_valuations: number; review_cases: number;
  last_import_at: string | null; last_valuation_at: string | null;
  price_source: { mode: string; available: boolean };
};

function backendErrorMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (typeof detail !== "object" || detail === null) {
    return "Die Anfrage ist fehlgeschlagen.";
  }
  const problem = detail as {
    message?: unknown;
    line?: unknown;
    field?: unknown;
    errors?: unknown;
  };
  const message =
    typeof problem.message === "string"
      ? problem.message
      : "Die Anfrage ist fehlgeschlagen.";
  const position =
    typeof problem.line === "number"
      ? ` Zeile ${problem.line}${typeof problem.field === "string" ? `, Feld ${problem.field}` : ""}.`
      : "";
  return `${message}${position}`;
}

function isSafeProviderProblem(detail: unknown): boolean {
  if (typeof detail !== "object" || detail === null) return false;
  const code = (detail as { code?: unknown }).code;
  return typeof code === "string" && code.startsWith("kraken_");
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  try {
    const response = await fetch(apiUrl(path), { ...options, signal });
    const body = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    if (!response.ok) {
      if (response.status >= 500 && !isSafeProviderProblem(body.detail)) {
        throw new Error(
          "Das Backend hat die Anfrage nicht verarbeiten können.",
        );
      }
      throw new Error(backendErrorMessage(body.detail));
    }
    return body as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    if (error instanceof TypeError) {
      throw new Error(
        "Die Verbindung zum Backend konnte nicht hergestellt werden.",
      );
    }
    throw error;
  }
}
