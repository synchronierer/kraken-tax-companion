import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve, sep } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const repositoryRoot = process.env.SPRINT3A_REPOSITORY_ROOT
  ? pathToFileURL(`${resolve(process.env.SPRINT3A_REPOSITORY_ROOT)}${sep}`)
  : new URL("../../", import.meta.url);

const api = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const nginx = readFileSync(new URL("../nginx.conf", import.meta.url), "utf8");
const compose = readFileSync(
  new URL("docker-compose.yml", repositoryRoot),
  "utf8",
);
const backend = readFileSync(
  new URL("backend/app/main.py", repositoryRoot),
  "utf8",
);
const uiModels = readFileSync(
  new URL("../src/ui-models.ts", import.meta.url),
  "utf8",
);

test("production client defaults to same-origin and normalizes paths", () => {
  assert.match(api, /configuredBaseUrl\.trim\(\)\.replace\(\/\\\/\+\$\/, ""\)/);
  assert.match(api, /path\.replace\(\/\^\\\/\+\/, ""\)/);
  assert.doesNotMatch(api, /http:\/\/localhost:8000|127\.0\.0\.1:8000|backend:8000/);
  assert.match(compose, /VITE_API_BASE_URL: \$\{VITE_API_BASE_URL:-\}/);
});

test("network failures have a comprehensible German message", () => {
  assert.match(
    api,
    /Die Verbindung zum Backend konnte nicht hergestellt werden\./,
  );
  assert.match(app, /\/api\/dashboard/);
  assert.match(app, /\/api\/system\/status/);
  assert.match(api, /Das Backend hat die Anfrage nicht verarbeiten können\./);
  assert.match(api, /response\.status >= 500/);
});

test("nginx preserves the API prefix and retains the SPA fallback", () => {
  assert.match(nginx, /location \/api\/ \{/);
  assert.match(nginx, /proxy_pass http:\/\/backend:8000;/);
  assert.doesNotMatch(nginx, /proxy_pass http:\/\/backend:8000\/;/);
  assert.match(nginx, /try_files \$uri \$uri\/ \/index\.html;/);
  assert.match(nginx, /location = \/backend-health/);
  assert.match(nginx, /proxy_pass http:\/\/backend:8000\/health;/);
});

test("backend CORS stays restricted", () => {
  assert.doesNotMatch(backend, /allow_origins\s*=\s*\["\*"\]/);
  assert.match(backend, /allow_origins=settings\.cors_origins/);
});

test("production UI has no mock data, injected HTML or valuation formula", () => {
  assert.doesNotMatch(app, /dangerouslySetInnerHTML|localStorage|Mockdaten/i);
  assert.doesNotMatch(app, /eur_value\\s*=|quantity\\s*\\*\\s*(?:price|unit)/i);
  assert.match(uiModels, /validateManualPrice/);
  assert.match(uiModels, /safeSystemEntries/);
});

test("Sprint 3B navigation uses backend tax and export contracts", () => {
  for (const label of [
    "Steuerübersicht",
    "FIFO-Zuordnungen",
    "Bestände",
    "Steuerjournal",
    "Exporte",
  ]) {
    assert.match(app, new RegExp(label));
  }
  for (const endpoint of [
    "/api/tax-summary",
    "/api/tax-calculations",
    "/api/inventory-lots",
    "/api/lot-allocations",
    "/api/tax-journal",
    "/api/exports",
  ]) {
    assert.match(app, new RegExp(endpoint));
  }
  assert.doesNotMatch(app, /gain_loss_eur\s*=|proceeds_eur\s*-/);
  assert.doesNotMatch(app, /http:\/\/localhost|http:\/\/backend/);
});

test("Kraken API comparison gates the confirmed read-only import", () => {
  for (const contract of [
    "Kraken API",
    "/api/kraken/connection",
    "/api/kraken/ledger-compare",
    "/api/kraken/ledger-import",
    "ready_for_import",
    "explicit_confirmation:true",
    "expected_ledger_id_digest",
    "transform:false",
  ]) {
    assert.match(app, new RegExp(contract));
  }
  assert.match(app, /disabled=\{busy\|\|comparison\.ready_for_import!==true\|\|!confirmed\}/);
  assert.doesNotMatch(app, /KRAKEN_API_(?:KEY|SECRET)|withdraw|deposit|trade-order/i);
});
