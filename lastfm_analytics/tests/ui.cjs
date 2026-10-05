// Run against an empty development instance with the fictional demo available.
const assert = require("node:assert/strict");
const path = require("node:path");
const os = require("node:os");
const fs = require("node:fs");
const output =
  process.env.SCREENSHOT_DIR ||
  path.join(os.tmpdir(), "listening-analytics-ui");
fs.mkdirSync(output, { recursive: true });
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || undefined,
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1050 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(process.env.APP_URL || "http://127.0.0.1:8105/");
  await page.getByRole("link", { name: "Explore a fictional demo" }).waitFor();
  await page.getByRole("link", { name: "Explore a fictional demo" }).click();
  await page.locator("#content .metrics").waitFor();
  assert.match(await page.locator("#banner").innerText(), /Fictional demo/);
  await page.screenshot({
    path: path.join(output, "overview-light.png"),
    fullPage: true,
  });
  // Date drill-down and search are functional, not decorative.
  await page.locator(".bar-chart button").nth(3).click();
  await page.locator("#content table").waitFor();
  assert.equal(
    await page.locator("#page-title").innerText(),
    "Listening history",
  );
  assert.equal(await page.locator("#period").inputValue(), "custom");
  await page.locator('#nav [data-view="songs"]').click();
  await page.locator("#period").selectOption("all");
  await page.locator("#search").fill("Come Together");
  await page.waitForTimeout(450);
  await page.waitForFunction(
    () =>
      document.querySelector("#content").getAttribute("aria-busy") === "false",
  );
  await page
    .getByRole("button", { name: "Come Together", exact: true })
    .click();
  await page.locator("#detail-dialog .table-wrap").waitFor();
  assert.match(
    await page.locator("#detail-dialog").innerText(),
    /2009 Remaster/,
  );
  // Manual separate and undo restore all-time combined rankings.
  await page.locator("#detail-dialog [data-separate]").first().click();
  await page.locator("#confirm-ok").click();
  await page.waitForFunction(
    () => !document.querySelector("#detail-dialog").open,
  );
  await page.locator(".settings-nav").click();
  await page.locator("#undo:not([disabled])").waitFor();
  await page.locator("#undo").click();
  await page.waitForFunction(() => document.querySelector("#undo")?.disabled);
  // Merge groups and undo, using a live performance and studio song by same artist.
  await page.locator("#search").fill("Come as You Are");
  await page.waitForTimeout(450);
  await page.waitForFunction(
    () => document.querySelectorAll(".group-select").length === 2,
  );
  await page.locator(".group-select").nth(0).check();
  await page.locator(".group-select").nth(1).check();
  await page.locator("#merge").click();
  await page.locator("#confirm-ok").click();
  await page.waitForFunction(
    () => document.querySelectorAll(".group-select").length === 1,
  );
  await page.locator("#undo").click();
  await page.waitForFunction(
    () => document.querySelectorAll(".group-select").length === 2,
  );
  // Mobile and dark theme, accessible navigation, no page overflow.
  await page.locator('#nav [data-view="overview"]').click();
  await page.locator("#period").selectOption("30d");
  await page.locator("#theme").click();
  await page.locator("#content .metrics").waitFor();
  await page.screenshot({
    path: path.join(output, "overview-dark.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(output, "overview-mobile.png"),
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
    false,
  );
  await page.locator('#nav [data-view="trends"]').click();
  await page.locator(".heatmap").waitFor();
  assert.equal(await page.locator(".heatmap .cell").count(), 168);
  await page.locator('#nav [data-view="history"]').click();
  await page.locator("#content table").waitFor();
  await page.locator("#search").fill("<img src=x onerror=alert(1)>");
  await page.waitForTimeout(450);
  assert.equal(await page.locator("#content img").count(), 0);
  assert.deepEqual(errors, []);
  console.log(
    "UI passed: setup, demo, dates, history, search, detail, separate, merge, undo, mobile, dark, heatmap and safe text rendering.",
  );
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
