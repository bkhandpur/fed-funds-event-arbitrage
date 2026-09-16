import { expect, test } from "@playwright/test";

test("primary decision remains readable on mobile", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "NO TRADE" })).toBeVisible();
  const viewport = page.viewportSize();
  expect(viewport?.width).toBeLessThan(700);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});
