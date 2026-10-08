import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const consoleErrors = new WeakMap<object, string[]>();

test.beforeEach(async ({ page }) => {
  await page.route("**/api/meetings?**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, meetings: [{ decision_date: "2099-01-28" }] }),
    }),
  );
  const errors: string[] = [];
  consoleErrors.set(page, errors);
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
});

test.afterEach(async ({ page }) => {
  expect(consoleErrors.get(page) ?? []).toEqual([]);
});

test("historical case study exposes decision and provenance", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "NO TRADE" })).toBeVisible();
  await expect(page.getByText("No executable trade under the supplied evidence.")).toBeVisible();
  await page.getByRole("button", { name: "Market Inputs" }).click();
  await expect(page.getByText("ZQU26.CBT")).toBeVisible();
  await expect(page.getByText("Historical user-supplied observation").first()).toBeVisible();
  await expect(page.getByText("Unavailable").first()).toBeVisible();
});

test("manual scenario validates through the Python API", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Manual scenario" }).click();
  await page.getByRole("button", { name: "Run scenario" }).click();
  await expect(page.getByText("No executable trade under the supplied evidence.")).toBeVisible();
  await expect(
    page.getByText("Settlement definitions have not been confirmed compatible."),
  ).toBeVisible();
});

test("live-data unavailable state is useful", async ({ page }) => {
  await page.route("**/api/live?**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: false,
        mode: "live",
        status: "unavailable",
        analysis: null,
        data_quality: {},
        provenance: [],
        error: { code: "MARKET_UNAVAILABLE", message: "no open Kalshi market mapped to +25 bp" },
      }),
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Live public data" }).click();
  await expect(
    page.getByRole("heading", { name: "No analysis is available for this selection." }),
  ).toBeVisible();
  await expect(page.getByText("No quote or depth has been inferred.")).toBeVisible();
});

test("live-data success preserves no-trade hard gates", async ({ page }) => {
  const caseStudy = await (await page.request.get("/api/case-study")).json();
  caseStudy.mode = "live";
  await page.route("**/api/live?**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(caseStudy),
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Live public data" }).click();
  await expect(
    page.getByText(
      "Live means recently retrieved public data. Executability requires additional evidence.",
    ),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "NO TRADE" })).toBeVisible();
});

test("overview has no serious accessibility violations", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "NO TRADE" })).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? "")),
  ).toEqual([]);
});

test("a late live response cannot replace a newer case study", async ({ page }) => {
  const live = await (await page.request.get("/api/case-study")).json();
  live.mode = "live";
  let release: (() => void) | undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/live?**", async (route) => {
    await held;
    await route
      .fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(live) })
      .catch(() => {});
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "NO TRADE" })).toBeVisible();
  const requested = page.waitForRequest("**/api/live?**");
  await page.getByRole("button", { name: "Live public data" }).click();
  await requested;
  await expect(page.getByRole("heading", { name: "NO TRADE" })).toHaveCount(0);
  await page.getByRole("button", { name: "Historical case study" }).click();
  await expect(page.getByRole("heading", { name: "NO TRADE" })).toBeVisible();
  release!();
  await expect(page.locator(".decision-card")).toContainText("Case study");
  await page.getByRole("button", { name: "Historical case study" }).click();
  await expect(page.getByRole("heading", { name: "NO TRADE" })).toBeVisible();
});
test("manual edit keeps the previous entered values", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Manual scenario" }).click();
  await page.locator('[name="contracts"]').fill("250");
  await page.getByRole("button", { name: "Run scenario" }).click();
  await expect(page.getByRole("heading", { name: "NO TRADE" })).toBeVisible();
  await page.getByRole("button", { name: "Edit manual scenario" }).click();
  await expect(page.locator('[name="contracts"]')).toHaveValue("250");
});

for (const width of [390, 768, 1280, 1440])
  test(`all research sections fit ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    expect(page.viewportSize()?.width).toBe(width);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "NO TRADE" })).toBeVisible();
    for (const name of [
      "Overview",
      "Market Inputs",
      "Probability Decomposition",
      "Trade Construction",
      "State Payoffs",
      "Risk and Data Quality",
      "Methodology",
    ]) {
      await page.getByRole("button", { name, exact: true }).click();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    }
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    await page.screenshot({
      path: width === 1440 ? "public/dashboard-preview.png" : `test-results/dashboard-${width}.png`,
      fullPage: true,
    });
  });
