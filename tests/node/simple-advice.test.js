// Run with: node --test tests/node/simple-advice.test.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const script = fs.readFileSync(path.join(root, "simple-app.js"), "utf8");
const numericIds = [
  "simple-median-value", "simple-min-value", "simple-max-value",
  "simple-confirmation-rate", "simple-histogram-min", "simple-histogram-max",
];
const textOnly = (value) => value.replace(/<[^>]*>/g, "").trim();
const flush = () => new Promise(setImmediate);

function advice(analysisDate = "2026-09-30") {
  const summary = { analysis_date: analysisDate, fuels: {}, savings_distributions: {} };
  for (const [fuel, median] of Object.entries({ diesel: 0.24, e10: 0.25, e5: 0.25 })) {
    const savings = { minimum: 0, maximum: 0.5, median, average: 0.26, p95: 0.45 };
    summary.fuels[fuel] = {
      confirmation_rate: 0.99, confirmed_station_fuels: 99,
      eligible_station_fuels: 100, excluded_station_fuels: 2,
      savings_eur_per_liter: savings,
    };
    summary.savings_distributions[fuel] = {
      savings_eur_per_liter: savings, sample_size: 100, bin_width_eur_per_liter: 0.05,
      bins: Array.from({ length: 10 }, (_, index) => ({
        lower: index * 0.05, upper: (index + 1) * 0.05, count: 10,
      })),
    };
  }
  return summary;
}

// Minimal DOM harness: values come from the real HTML and mutations from the
// unmodified browser script. No browser, network, or third-party dependencies.
function start(fetch) {
  const elements = new Map();
  for (const id of [...script.matchAll(/getElementById\("([^"]+)"\)/g)].map((m) => m[1])) {
    const match = html.match(new RegExp(`<([\\w]+)\\b([^>]*\\bid="${id}"[^>]*)>([\\s\\S]*?)<\\/\\1>`));
    assert.ok(match, `HTML contains ${id}`);
    elements.set(id, { textContent: textOnly(match[3]), innerHTML: match[3],
      hidden: /\bhidden\b/.test(match[2]), style: {}, attributes: {},
      setAttribute(name, value) { this.attributes[name] = value; },
      classList: { add() {}, remove() {} },
    });
  }
  const buttons = ["diesel", "e10", "e5"].map((fuel) => ({
    dataset: { analysisFuel: fuel }, attributes: {},
    classList: { toggle() {} },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(event, listener) { this[event] = listener; },
  }));
  vm.runInNewContext(script, {
    Intl, Date, fetch, console: { warn() {} },
    window: { location: { hostname: "localhost" }, setInterval() {},
      TankzeitWebConfig: { adviceUrl: "https://example.test/latest.json" } },
    document: {
      getElementById: (id) => { assert.ok(elements.has(id), id); return elements.get(id); },
      querySelectorAll: () => buttons,
    },
  });
  return { get: (id) => elements.get(id), click: (fuel) => buttons.find((b) => b.dataset.analysisFuel === fuel).click() };
}

const ok = (data) => ({ ok: true, json: async () => data });
function placeholders(app) {
  for (const id of numericIds) assert.equal(app.get(id).textContent, "–", id);
  assert.equal(app.get("simple-median-marker").hidden, true);
}

for (const timezone of ["UTC", "Europe/Berlin", "America/Los_Angeles", "America/New_York", "Pacific/Honolulu", "Asia/Tokyo"]) {
  for (const [date, display] of [["2026-09-30", "30.09.2026"], ["2027-01-01", "01.01.2027"]]) {
    test(`${timezone}: preserves date-only ${date}`, async () => {
      const previous = process.env.TZ;
      try {
        process.env.TZ = timezone;
        const app = start(async () => ok(advice(date)));
        await flush();
        assert.ok(app.get("simple-audit-meta").textContent.includes(`Nachtanalyse vom ${display}`));
        assert.equal(app.get("simple-median-value").textContent, 24);
      } finally {
        if (previous === undefined) delete process.env.TZ;
        else process.env.TZ = previous;
      }
    });
  }
}

test("pending load has no stale numbers and renders the selected fuel on success", async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const app = start(() => pending);
  placeholders(app);
  for (const id of ["simple-audit-meta", "simple-confirmation-copy", "simple-histogram"]) {
    assert.match(app.get(id).textContent, /wird geladen/);
  }
  app.click("e10");
  assert.equal(app.get("simple-metric-scope").textContent, "E10");
  placeholders(app);
  release(ok(advice()));
  await flush();
  assert.equal(app.get("simple-median-value").textContent, 25);
  assert.equal(app.get("simple-median-marker").hidden, false);
  assert.equal(app.get("simple-median-marker").style.left, "50%");
  assert.equal(app.get("simple-confirmation-rate").textContent, "99,00 %");
  assert.match(app.get("simple-confirmation-copy").textContent, /99 von 100/);
  assert.equal(app.get("simple-max-value").textContent, "50 ct");
  assert.equal(app.get("simple-histogram-min").textContent, "0 ct/L");
  assert.equal(app.get("simple-histogram-max").textContent, "50 ct/L");
  assert.equal((app.get("simple-histogram").innerHTML.match(/class="simple-histogram-bar/g) || []).length, 10);
  assert.match(app.get("simple-stat-notes").innerHTML, /Median 25 ct\/L/);
  app.click("e5");
  assert.equal(app.get("simple-median-value").textContent, 25);
  app.click("diesel");
  assert.equal(app.get("simple-median-value").textContent, 24);
});

for (const failure of ["network", "HTTP", "invalid JSON"]) {
  test(`${failure} failure leaves no fake numbers or loading messages`, async () => {
    let requests = 0;
    const app = start(async () => {
      requests += 1;
      if (failure === "network") throw new Error("offline");
      if (failure === "HTTP") return { ok: false, status: 503 };
      return { ok: true, json: async () => { throw new SyntaxError("Invalid JSON"); } };
    });
    await flush();
    assert.equal(requests, 2);
    assert.equal(app.get("simple-audit-meta").textContent, "Die Nachtanalyse ist momentan nicht erreichbar.");
    placeholders(app);
    assert.match(app.get("simple-confirmation-copy").textContent, /nicht verfügbar/);
    assert.match(app.get("simple-histogram").innerHTML, /nicht verfügbar/);
    assert.match(app.get("simple-stat-notes").innerHTML, /nicht verfügbar/);
    app.click("e5");
    assert.equal(app.get("simple-metric-scope").textContent, "E5");
    placeholders(app);
  });
}

test("uses the fallback URL after the first source fails", async () => {
  const urls = [];
  const app = start(async (url, options) => {
    urls.push(url);
    assert.equal(options.cache, "no-store");
    if (urls.length === 1) throw new Error("offline");
    return ok(advice());
  });
  await flush();
  assert.deepEqual(urls, ["data/simple/latest.json", "https://example.test/latest.json"]);
  assert.equal(app.get("simple-median-value").textContent, 24);
  assert.match(app.get("simple-audit-meta").textContent, /30\.09\.2026/);
});
