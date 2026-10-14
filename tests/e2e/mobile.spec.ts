import { expect, test } from "@playwright/test";
test("primary analytical numbers precede controls on mobile", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Scenario workbench" })).toBeVisible();
  expect(await page.evaluate(() => innerWidth)).toBeLessThan(700);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(
    await page
      .locator(".wb-results")
      .boundingBox()
      .then((b) => b!.y),
  ).toBeLessThan(
    await page
      .locator(".wb-inputs")
      .boundingBox()
      .then((b) => b!.y),
  );
});
