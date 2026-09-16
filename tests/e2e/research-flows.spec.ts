import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const consoleErrors = new WeakMap<object, string[]>();

test.beforeEach(async ({ page }) => {
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
  await page.getByRole("button", { name: "Run Python model" }).click();
  await expect(page.getByText("No executable trade under the supplied evidence.")).toBeVisible();
  await expect(page.getByText("Settlement definitions have not been confirmed compatible.")).toBeVisible();
});

test("live-data unavailable state is useful", async ({ page }) => {
  await page.route("**/api/live?**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ ok: false, mode: "live", status: "unavailable", analysis: null, data_quality: {}, provenance: [], error: { code: "MARKET_UNAVAILABLE", message: "no open Kalshi market mapped to +25 bp" } }),
  }));
  await page.goto("/");
  await page.getByRole("button", { name: "Live public data" }).click();
  await expect(page.getByRole("heading", { name: "No analysis is available for this selection." })).toBeVisible();
  await expect(page.getByText("No quote or depth has been inferred.")).toBeVisible();
});

test("live-data success preserves no-trade hard gates", async ({ page }) => {
  const caseStudy = await (await page.request.get("/api/case-study")).json();
  caseStudy.mode = "live";
  await page.route("**/api/live?**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(caseStudy) }));
  await page.goto("/");
  await page.getByRole("button", { name: "Live public data" }).click();
  await expect(page.getByText("Live means recently retrieved public data—not guaranteed executable data.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "NO TRADE" })).toBeVisible();
});

test("overview has no serious accessibility violations", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "NO TRADE" })).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""))).toEqual([]);
});
