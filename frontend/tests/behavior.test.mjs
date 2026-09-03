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
            code: "kraken_unavailable",
            message: "Kraken ist vorübergehend nicht erreichbar.",
          },
        }),
        { status: 503, headers: { "content-type": "application/json" } },
      );
    await assert.rejects(
      module.request("/api/kraken-sync"),
      /Kraken ist vorübergehend nicht erreichbar/,
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
  assert.equal(
    model.formatEurDecimal("11.964719979423988667047664309258439466617"),
    "11,96 €",
  );
  assert.equal(model.formatEurDecimal("0.0140694201443203559150371395821465577"), "0,01 €");
  assert.equal(model.formatAssetQuantity("0.3560326900"), "0,35603269");
  assert.equal(model.formatAssetQuantity("0.1280751569"), "0,12807516");
  assert.equal(model.sumDecimalStrings([]), "0");
  assert.equal(model.sumDecimalStrings(["1", "2"]), "3");
  assert.equal(model.sumDecimalStrings(["0.1", "0.2"]), "0.3");
  assert.equal(
    model.sumDecimalStrings(["1E-20", "2E-20"]),
    "0.00000000000000000003",
  );
  assert.equal(model.sumDecimalStrings(["1.23E-7", "0.000000077"]), "0.0000002");
  assert.equal(model.sumDecimalStrings(["2.5E+3", "500"]), "3000");
  assert.equal(model.sumDecimalStrings(["-2.5E-3", "0.003"]), "0.0005");
  assert.equal(model.sumDecimalStrings(["0E-39", "1.25"]), "1.25");
  assert.equal(
    model.sumDecimalStrings([
      "11.900000000000000000000000000000000000001",
      "0.064719979423988667047664309258439466616",
    ]),
    "11.964719979423988667047664309258439466617",
  );
  assert.equal(
    model.sumDecimalStrings([
      "1E-20", "1.23e-7", "0.0140694201443203559150371395821465577",
      "2.5E+3", "-2.5e-3", "0E-39",
    ]),
    "2500.0115695431443203559250371395821465577",
  );
  for (const invalid of ["NaN", "Infinity", "", "1,25", "abc"]) {
    assert.throws(() => model.sumDecimalStrings([invalid]), /Ungültiger Decimal-String/);
  }
  assert.equal(
    model.reviewDecisionLabel("include_as_werbungskosten"),
    "als Werbungskosten berücksichtigen",
  );
  assert.equal(
    model.reviewDecisionLabel("exclude_from_werbungskosten"),
    "nicht als Werbungskosten berücksichtigen",
  );
  assert.equal(model.reviewSubmissionAction(0, "", ""), "invalid");
  assert.equal(
    model.reviewSubmissionAction(1, "include_as_werbungskosten", "Begründung"),
    "submit",
  );
  assert.equal(
    model.reviewSubmissionAction(48, "include_as_werbungskosten", "Begründung"),
    "confirm",
  );
});

test("staking fee review requires an explicit decision and separate tax run", async () => {
  const source = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(source, /Staking-Plattformgebühren prüfen/);
  assert.match(source, /Keine Entscheidung ausgewählt/);
  assert.match(source, /Als Werbungskosten berücksichtigen/);
  assert.match(source, /Nicht als Werbungskosten berücksichtigen/);
  assert.match(source, /Begründung/);
  assert.match(source, /reviewSubmissionAction\(selected\.length,decision,reason\)/);
  assert.match(source, /Sammelentscheidung bestätigen/);
  assert.match(source, /Fälle mit insgesamt/);
  assert.match(source, /Abbrechen/);
  assert.match(source, /disabled={!selected\.length\|\|!decision\|\|!reason\.trim\(\)}/);
  assert.match(source, /formatEurDecimal\(item\.fee_value_eur\)/);
  assert.match(source, /formatAssetQuantity\(item\.fee_quantity\)/);
  assert.equal((source.match(/request<Row>\("\/api\/tax-review-decisions\/bulk"/g) ?? []).length, 1);
  assert.match(source, /neuer Taxlauf erforderlich/);
  assert.doesNotMatch(source, /tax-review-decisions[\s\S]{0,500}tax-calculations/);
});

test("export list exposes the independent format version", async () => {
  const source = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(source, /"format_version"/);
});

test("Kraken sync remains a manual isolated workflow", async () => {
  const source = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(source, /Kraken synchronisieren/);
  assert.match(source, /Letzter erfolgreicher Sync/);
  assert.match(source, /Nächster Abrufzeitraum/);
  assert.match(source, /Synchronisierung läuft/);
  assert.match(source, /Neu importiert/);
  assert.match(source, /Neue Domainobjekte/);
  assert.match(source, /Reviews/);
  assert.match(source, /disabled={busy\|\|Boolean\(syncState\.data\?\.processing_sync\)}/);
  assert.match(source, /request<Row>\("\/api\/kraken-sync",\{method:"POST"\}\)/);
  assert.doesNotMatch(source.match(/async function synchronize\(\).*?\n/)?.[0] ?? "", /valuations|tax-calculations|exports|tax-review/);
});
