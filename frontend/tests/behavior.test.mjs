import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { transform } from "esbuild";

async function importTypeScript(path, replacements = []) {
  let source = await readFile(new URL(path, import.meta.url), "utf8");
  for (const [pattern, value] of replacements) source = source.replace(pattern, value);
  const built = await transform(source, { format: "esm", loader: "ts", target: "es2022" });
  return import(`data:text/javascript;base64,${Buffer.from(built.code).toString("base64")}`);
}

test("api client composes same-origin paths and distinguishes failures", async () => {
  const module = await importTypeScript("../src/api.ts", [
    [/import\.meta\.env\.VITE_API_BASE_URL/, '""'],
  ]);
  assert.equal(module.apiUrl("api/dashboard"), "/api/dashboard");
  assert.equal(module.apiUrl("//api/imports"), "/api/imports");

  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({ detail: { code: "internal_server_error" } }),
        { status: 500, headers: { "content-type": "application/json" } },
      );
    await assert.rejects(
      module.request("/api/dashboard"),
      /Das Backend hat die Anfrage nicht verarbeiten können/,
    );
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({
          detail: {
            code: "manual_csv_invalid",
            message: "Die Kurs-CSV ist ungültig.",
            line: 3,
            field: "price_eur",
          },
        }),
        { status: 422, headers: { "content-type": "application/json" } },
      );
    await assert.rejects(
      module.request("/api/prices/manual/csv"),
      /Zeile 3, Feld price_eur/,
    );
    globalThis.fetch = async () => {
      throw new TypeError("synthetic fetch failure");
    };
    await assert.rejects(
      module.request("/api/dashboard"),
      /Die Verbindung zum Backend konnte nicht hergestellt werden/,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("presentation rules cover empty state, validation, reviews and secrets", async () => {
  const model = await importTypeScript("../src/ui-models.ts");
  assert.equal(
    model.isEmptyDashboard({
      imports: 0,
      raw_records: 0,
      rewards: 0,
      trades: 0,
      resolved_valuations: 0,
      open_valuations: 0,
      review_cases: 0,
    }),
    true,
  );
  assert.equal(
    model.validateManualPrice({
      asset: "BTC",
      date: "2026-07-01",
      price_eur: "123.450000000000000001",
      source: "Beleg",
      reason: "Korrektur",
    }),
    null,
  );
  assert.match(
    model.validateManualPrice({
      asset: "",
      date: "",
      price_eur: "0",
      source: "",
      reason: "",
    }),
    /Pflichtfelder/,
  );
  assert.match(
    model.validateManualPrice({
      asset: "BTC",
      date: "2026-07-01",
      price_eur: "-1",
      source: "Beleg",
      reason: "Korrektur",
    }),
    /positive Dezimalzahl/,
  );
  assert.equal(model.reviewGroup("valuation_incomplete_daily_coverage"), "Unvollständige Kursdaten");
  assert.deepEqual(
    model.safeSystemEntries({
      backend: true,
      api_key_configured: true,
      api_key: "must-not-be-rendered",
    }),
    [["backend", true], ["api_key_configured", true]],
  );
  assert.equal(model.pageQuery(-1, 1000), "offset=0&limit=200");
});

test("staking fee review requires an explicit decision and separate tax run", async () => {
  const source = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(source, /Staking-Plattformgebühren prüfen/);
  assert.match(source, /Keine Entscheidung ausgewählt/);
  assert.match(source, /Als Werbungskosten berücksichtigen/);
  assert.match(source, /Nicht als Werbungskosten berücksichtigen/);
  assert.match(source, /Begründung/);
  assert.match(source, /neuer Taxlauf erforderlich/);
  assert.doesNotMatch(source, /tax-review-decisions[\s\S]{0,500}tax-calculations/);
});
