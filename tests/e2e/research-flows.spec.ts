import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs/promises";

const errors = new WeakMap<object, string[]>();
test.beforeEach(async ({ page }) => {
  const list: string[] = [];
  errors.set(page, list);
  page.on("pageerror", (e) => list.push(e.message));
});
test.afterEach(async ({ page }) => expect(errors.get(page)).toEqual([]));

async function reference(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Reference A", exact: true }).click();
  await expect(
    page.locator(".wb-context").getByText("Synthetic reference A", { exact: true }),
  ).toBeVisible();
}

test("reference arithmetic, changed costs, signed chart, comparison and exports", async ({
  page,
}) => {
  await reference(page);
  await expect(page.locator(".wb-metric").filter({ hasText: "Binary net model EV" })).toContainText(
    "$1.26",
  );
  await expect(
    page.locator(".wb-metric").filter({ hasText: "Combined net model EV" }),
  ).toContainText("-$15.198");
  await expect(page.getByRole("img", { name: /Selected combined payoffs/ })).toBeVisible();
  await expect(page.getByRole("region", { name: "State cashflow ledger" })).toContainText(
    "-$25.635",
  );
  await page.getByRole("button", { name: "Save displayed base" }).click();
  await page.getByRole("button", { name: "Clone to manual" }).click();
  await page.getByLabel("Binary entry slippage dollars", { exact: true }).fill(".01");
  await expect(page.getByText("Draft changed · displayed result is stale")).toBeVisible();
  await page.getByRole("button", { name: "Run scenario", exact: true }).click();
  await expect(
    page.locator(".wb-metric").filter({ hasText: "Combined net model EV" }),
  ).toContainText("-$20.198");
  await expect(page.getByText(/Combined model EV delta/)).toContainText("-$5.00");
  const json = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export request and result JSON" }).click();
  const output = await json;
  const saved = await output.path();
  const value = JSON.parse(await fs.readFile(saved!, "utf8"));
  expect(value.schema_version).toBe("2.0");
  expect(value.result.workbench.selected.economics.combined_model_ev_dollars).toBeCloseTo(
    -20.1975,
    8,
  );
  const api = await page.request.post("/api/analyze", { data: { analysis: value.request } });
  expect(
    (await api.json()).analysis.workbench.selected.economics.combined_model_ev_dollars,
  ).toBeCloseTo(-20.1975, 8);
  const csv = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export ledger CSV" }).click();
  const data = await fs.readFile((await (await csv).path())!, "utf8");
  const lines = data.split("\n");
  const keys = lines[0].split(",");
  for (const line of lines.slice(1)) {
    const row = Object.fromEntries(line.split(",").map((v, i) => [keys[i], Number(v)]));
    expect(row.combined_net_pnl_dollars).toBeCloseTo(
      row.kalshi_settlement_dollars -
        row.kalshi_acquisition_cost_dollars -
        row.kalshi_fees_dollars +
        row.futures_gross_pnl_dollars -
        row.futures_costs_dollars,
      8,
    );
  }
  await page.getByRole("button", { name: "Unhedged", exact: true }).click();
  await expect(
    page.locator(".wb-metric").filter({ hasText: "Combined net model EV" }),
  ).toContainText("-$3.74");
  await page.getByRole("button", { name: "Restore base", exact: true }).click();
  await expect(
    page.locator(".wb-metric").filter({ hasText: "Combined net model EV" }),
  ).toContainText("-$15.198");
});

test("stress cells and evidence failures remain visible", async ({ page }) => {
  await reference(page);
  await page.getByRole("button", { name: "-$67.305", exact: true }).click();
  await expect(page.getByRole("region", { name: "State cashflow ledger" })).toContainText(
    "-$67.305",
  );
  await page.getByText("Execution evidence and provenance", { exact: true }).click();
  await expect(page.getByRole("region", { name: "Execution evidence" })).toContainText(
    "Futures selected depth",
  );
  await expect(page.getByRole("region", { name: "Execution evidence" })).toContainText("unknown");
  await page.getByLabel("ZQ symbol", { exact: true }).fill("ZQG27.CBT");
  await page.getByRole("button", { name: "Run scenario", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Execution evidence" })
      .getByRole("row")
      .filter({ hasText: "Contract month" }),
  ).toContainText("fail");
});

test("infeasible tails and fractional sizes fail through real API", async ({ page }) => {
  await reference(page);
  await page.getByLabel("Signed futures contracts", { exact: true }).fill("1.5");
  await page.getByRole("button", { name: "Run scenario", exact: true }).click();
  await expect(page.locator("main").getByRole("alert")).toBeVisible();
  await page.getByLabel("Signed futures contracts", { exact: true }).fill("1");
  await page.getByLabel("Probability mode", { exact: true }).selectOption("bounds");
  await page.getByLabel("Lower probability state 1", { exact: true }).fill(".8");
  await page.getByLabel("Lower probability state 2", { exact: true }).fill(".8");
  await page.getByRole("button", { name: "Run scenario", exact: true }).click();
  await expect(page.locator("main").getByRole("alert")).toContainText("infeasible");
});

test("provider failure and delayed response preserve provenance", async ({ page }) => {
  await reference(page);
  const data = await (await page.request.get("/api/case-study?example=reference-a")).json();
  data.mode = "live";
  let release: () => void = () => {};
  const held = new Promise<void>((r) => (release = r));
  await page.route("**/api/live?**", async (route) => {
    await held;
    await route.fulfill({ json: data }).catch(() => {});
  });
  const request = page.waitForRequest("**/api/live?**");
  await page.getByRole("button", { name: "Retrieve indicative live" }).click();
  await request;
  await page.getByRole("button", { name: "September example", exact: true }).click();
  release();
  await expect(
    page.locator(".wb-context").getByText("Historical September", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".wb-context")).toContainText("2026-09-16");
  await page.unroute("**/api/live?**");
  await page.route("**/api/live?**", (route) =>
    route.fulfill({ json: { ok: false, error: { message: "Provider unavailable" } } }),
  );
  await page.getByRole("button", { name: "Retrieve indicative live" }).click();
  await expect(page.locator("main").getByRole("alert")).toContainText("Provider unavailable");
  await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
});

for (const width of [390, 768, 1280, 1440])
  test(`workspace at actual width ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 950 });
    await reference(page);
    expect(await page.evaluate(() => innerWidth)).toBe(width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    const table = page.getByRole("region", { name: "Hedge candidate comparison" });
    await table.focus();
    await expect(table).toBeFocused();
    await page.screenshot({ path: `test-results/fed-workspace-${width}.png`, fullPage: true });
    const a = await new AxeBuilder({ page }).analyze();
    expect(a.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""))).toEqual(
      [],
    );
  });
