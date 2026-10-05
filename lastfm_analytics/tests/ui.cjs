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
  // Candidates are reviewed separately from existing merged groups.
  await page.locator('[data-group-tab="skipped"]').click();
  await page.locator("#search").fill("Come as You Are");
  await page.waitForTimeout(450);
  await page.locator("[data-candidate-merge]").first().waitFor();
  const candidate = page.locator(".review-card").first();
  assert.equal(await candidate.locator("[data-candidate-merge]").isDisabled(), true);
  await candidate.locator(".candidate-version").nth(0).check();
  assert.equal(await candidate.locator("[data-candidate-merge]").isDisabled(), true);
  await candidate.locator(".candidate-version").nth(1).check();
  await candidate.locator(".candidate-name").fill("Come as You Are, chosen name");
  await candidate.locator("[data-candidate-merge]").click();
  assert.match(await page.locator("#confirm-dialog").innerText(), /chosen name/);
  await page.locator("#confirm-ok").click();
  await page.locator('[data-group-tab="merged"]').click();
  await page.locator("#search").fill("Come as You Are");
  await page.waitForTimeout(450);
  await page.locator(".review-card").first().waitFor();
  assert.match(await page.locator(".review-card").first().innerText(), /chosen name/);
  await page.locator("#undo").click();
  await page.locator('[data-group-tab="skipped"]').click();
  await page.locator("#search").fill("Come as You Are");
  await page.waitForTimeout(450);
  await page.locator("[data-candidate-merge]").first().waitFor();
  // Mobile and dark theme, accessible navigation, no page overflow.
  await page.locator('#nav [data-view="overview"]').click();
  await page.locator("#period").selectOption("30d");
  await page.locator(".settings-nav").click();
  await page.locator("#theme").click();
  await page.locator('#nav [data-view="overview"]').click();
  await page.locator("#content .metrics").waitFor();
  await page.screenshot({
    path: path.join(output, "overview-dark.png"),
    fullPage: true,
  });
  for (const width of [320, 390, 430]) {
    await page.setViewportSize({ width, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const buttons = await page.locator("#nav button").evaluateAll(nodes => nodes.map(n => {
      const r = n.getBoundingClientRect();
      return { width: r.width, height: r.height, bottom: r.bottom, top: r.top };
    }));
    assert.ok(buttons.every(b => b.width >= 44 && b.height >= 44 && b.bottom <= 844 && b.top > 700));
  }
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
  await page.locator(".settings-nav").click();
  await page.locator("#palette").selectOption("ocean");
  await page.locator('#nav [data-view="overview"]').click();
  assert.equal(await page.evaluate(() => document.documentElement.dataset.palette), "ocean");
  await page.route("**/api/overview?**", async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.timeline = Array.from({length:240},(_,i)=> {
      const year = 2006 + Math.floor(i/12), month = i%12+1;
      const prefix = `${year}-${String(month).padStart(2,"0")}`;
      return {label:prefix,start:`${prefix}-01`,end:`${prefix}-28`,plays:12};
    });
    await route.fulfill({response,json:data});
  });
  await page.locator("#period").selectOption("all");
  await page.waitForFunction(() => document.querySelectorAll("#content .bar-chart button").length === 240);
  for (const width of [320,390,1440]) {
    await page.setViewportSize({width,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.ok(await page.locator("#content .chart-scroll").evaluate(n=>n.scrollWidth>n.clientWidth));
    if (width <= 620) assert.equal(await page.locator("#content .calendar-scroll").evaluate(n=>n.scrollWidth>n.clientWidth), false);
  }
  const before = await page.locator("#content .bar-chart").evaluate(n=>n.scrollWidth);
  await page.locator('#content [data-chart-zoom="in"]').click();
  await page.locator('#content [data-chart-zoom="in"]').click();
  assert.ok(await page.locator("#content .bar-chart").evaluate(n=>n.scrollWidth)>before);
  assert.equal(await page.locator("#content .calendar-cell:not(.unavailable)").count(),240);
  await page.locator("#content .chart-scroll").evaluate(n=>n.scrollLeft=500);
  assert.ok(await page.locator("#content .chart-scroll").evaluate(n=>n.scrollLeft)>0);
  await page.setViewportSize({width:390,height:844});
  await page.locator('#content [data-chart-zoom="reset"]').click();
  const scroller = page.locator('#content .chart-scroll');
  await scroller.scrollIntoViewIfNeeded();
  await scroller.evaluate(n => n.scrollLeft = 300);
  const rect = await scroller.boundingBox();
  const centre = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setTouchEmulationEnabled', {enabled:true});
  const touches = distance => [{x:centre-distance,y,id:1},{x:centre+distance,y,id:2}];
  const pinchBefore = await scroller.locator('.bar-chart').evaluate(n => n.getBoundingClientRect().width);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:touches(30)});
  await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:touches(60)});
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  const pinchAfter = await scroller.locator('.bar-chart').evaluate(n => n.getBoundingClientRect().width);
  assert.ok(pinchAfter > pinchBefore * 1.8);
  assert.equal(await page.evaluate(() => visualViewport.scale),1);
  assert.equal(await page.locator('#page-title').innerText(),'Overview');
  const scrollBefore = await scroller.evaluate(n=>n.scrollLeft);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:centre+50,y,id:1}]});
  for (const x of [centre+30,centre,centre-30,centre-50])
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y,id:1}]});
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  assert.ok(await scroller.evaluate(n=>n.scrollLeft)>scrollBefore);
  await cdp.send('Emulation.setTouchEmulationEnabled', {enabled:false});
  await page.unroute("**/api/overview?**");
  await page.setViewportSize({width:390,height:844});
  await page.locator("#period").selectOption("30d");
  await page.locator("#content .list-row").first().click();
  await page.locator("#detail-dialog .calendar-heatmap").waitFor();
  const bounds = await page.locator("#detail-dialog").boundingBox();
  assert.equal(Math.round(bounds.width),390);
  assert.equal(Math.round(bounds.height),844);
  assert.ok(await page.evaluate(()=>document.body.classList.contains("modal-open")));
  await page.locator("#close-detail").click();
  await page.waitForFunction(()=>!document.body.classList.contains("modal-open"));
  const yearOption = await page.locator("#year-options option").first().getAttribute("value");
  await page.route("**/api/overview?**", async route => {
    await new Promise(resolve => setTimeout(resolve, 700));
    await route.continue();
  });
  await page.locator("#period").selectOption(yearOption);
  await page.locator("#loading-status").waitFor({state:"visible"});
  await page.waitForFunction(()=>document.querySelector("#content").getAttribute("aria-busy")==="false");
  await page.unroute("**/api/overview?**");
  await page.locator("#period").selectOption("30d");
  await page.locator('#nav [data-view="trends"]').click();
  await page.locator(".heatmap").waitFor();
  assert.equal(await page.locator('#nav [data-view="trends"]').getAttribute("aria-current"), "page");
  await page.locator("#period").selectOption("custom");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.locator("#period").selectOption("30d");
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
