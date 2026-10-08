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
  assert.equal(await page.locator("#period").inputValue(), "all");
  await page.screenshot({
    path: path.join(output, "overview-light.png"),
    fullPage: true,
  });
  // Artist logos occupy a full-width transparent stage, not a tinted thumbnail.
  const demoArtistLogo = "https://lastfm.freetls.fastly.net/i/u/174s/artist-fixture.png";
  await page.route("**/api/detail?*", async route => {
    if (new URL(route.request().url()).searchParams.get("entity") !== "artist")
      return route.continue();
    const response = await route.fetch();
    const detail = await response.json();
    detail.artist_logo = demoArtistLogo;
    detail.artist_photo = null;
    detail.artwork = null;
    detail.artwork_pending = false;
    return route.fulfill({response, json: detail});
  });
  await page.route("https://lastfm.freetls.fastly.net/**", route =>
    route.fulfill({ path: path.join(__dirname, "../analytics/static/icon-192.png"), contentType:"image/png" }));
  await page.locator('#content button[data-detail="artist"]').first().click();
  await page.locator("#artist-logo-slot img.artist-logo").waitFor();
  await page.locator(".spotify-link svg.spotify-mark").waitFor();
  assert.ok(await page.locator("#artist-logo-slot").evaluate(el =>
    el.getBoundingClientRect().width > 300 && getComputedStyle(el).backgroundColor === "rgba(0, 0, 0, 0)"));
  assert.equal(await page.locator(".spotify-link").evaluate(el =>
    getComputedStyle(el).backgroundColor), "rgb(30, 215, 96)");
  await page.locator("#close-detail").click();
  await page.unroute("**/api/detail?*");
  await page.unroute("https://lastfm.freetls.fastly.net/**");
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
  await page.route("**/api/detail?*", async (route) => {
    const response = await route.fetch();
    const detail = await response.json();
    detail.artwork = { url: "https://lastfm.freetls.fastly.net/i/u/174s/fixture.png", album: "Abbey Road", artist: "The Beatles" };
    await route.fulfill({ response, json: detail });
  });
  await page.route("https://lastfm.freetls.fastly.net/**", route => route.fulfill({ path: path.join(__dirname, "../analytics/static/icon-192.png"), contentType: "image/png" }));
  await page
    .getByRole("button", { name: "Come Together", exact: true })
    .click();
  await page.locator("#detail-dialog .table-wrap").waitFor();
  assert.match(
    await page.locator("#detail-dialog").innerText(),
    /2009 Remaster/,
  );
  const cover = page.locator(".detail-artwork img");
  await cover.waitFor();
  await page.waitForFunction(() => document.querySelector(".detail-artwork img")?.naturalWidth > 0);
  assert.match(await cover.getAttribute("alt"), /Abbey Road/);
  await page.setViewportSize({ width: 320, height: 700 });
  assert.equal(await page.locator("#detail-content").evaluate(el => el.scrollWidth <= el.clientWidth), true);
  await page.screenshot({ path: path.join(output, "cover-art-mobile.png") });
  await cover.evaluate(el => el.dispatchEvent(new Event("error")));
  assert.equal(await page.locator(".detail-artwork").count(), 0);
  assert.equal(await page.locator("#detail-dialog .metrics").isVisible(), true);
  await page.unroute("**/api/detail?*");
  await page.unroute("https://lastfm.freetls.fastly.net/**");
  await page.setViewportSize({ width: 1440, height: 1050 });
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
  await cdp.send('Emulation.setTouchEmulationEnabled', {enabled:true,maxTouchPoints:2});
  assert.equal(await page.evaluate(() => navigator.maxTouchPoints), 2);
  const touches = distance => [{x:centre-distance,y,id:1},{x:centre+distance,y,id:2}];
  // A notification from an earlier action must not intercept chart gestures.
  await page.locator('#toast').evaluate(n => n.hidden = false);
  assert.equal(await page.locator('#toast').evaluate(n => getComputedStyle(n).pointerEvents), 'none');
  assert.ok(await page.evaluate(({centre,y}) => [-30,30].every(offset =>
    document.elementFromPoint(centre+offset,y)?.closest('#content .chart-scroll')
  ), {centre,y}));
  const pinchBefore = await scroller.locator('.bar-chart').evaluate(n => n.getBoundingClientRect().width);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:touches(30)});
  // CDP input can be acknowledged before Chromium delivers the touch event.
  // Wait for each gesture phase before ending it or measuring its result.
  await page.waitForFunction(() => document.querySelector('#content .chart-scroll').dataset.gestureUntil === 'Infinity');
  await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:touches(60)});
  await page.waitForFunction(before => document.querySelector('#content .bar-chart').getBoundingClientRect().width > before * 1.8, pinchBefore);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await page.waitForFunction(() => document.querySelector('#content .chart-scroll').dataset.gestureUntil !== 'Infinity');
  const pinchAfter = await scroller.locator('.bar-chart').evaluate(n => n.getBoundingClientRect().width);
  assert.ok(pinchAfter > pinchBefore * 1.8);
  assert.equal(await page.evaluate(() => visualViewport.scale),1);
  assert.equal(await page.locator('#page-title').innerText(),'Overview');
  const scrollBefore = await scroller.evaluate(n=>n.scrollLeft);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:centre+50,y,id:1}]});
  for (const x of [centre+30,centre,centre-30,centre-50])
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y,id:1}]});
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await page.waitForFunction(before => document.querySelector('#content .chart-scroll').scrollLeft > before, scrollBefore);
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
  // Defaults, compact vinyl toggle, readable diary columns and in-app Back.
  await page.locator('#reset-filters').click();
  await page.waitForFunction(() => document.querySelector('#content').getAttribute('aria-busy') === 'false');
  assert.equal(await page.locator('#period').inputValue(),'all');
  assert.equal(await page.locator('#source').getAttribute('aria-pressed'),'false');
  assert.equal(await page.locator('#search').inputValue(),'');
  const songCell = await page.locator('.history-table tbody tr:not(.history-day)').first().locator('td').nth(1).boundingBox();
  assert.ok(songCell.width >= 260);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false);
  await page.locator('#nav [data-view="overview"]').click();
  await page.locator('#source').click();
  await page.waitForFunction(() => document.querySelector('#content').getAttribute('aria-busy') === 'false');
  assert.equal(await page.locator('#source').getAttribute('aria-pressed'),'true');
  await page.locator('#period').selectOption('30d');
  await page.locator('#reset-filters').click();
  await page.waitForFunction(() => document.querySelector('#content').getAttribute('aria-busy') === 'false');
  await page.locator('#content .metrics').waitFor();
  assert.equal(await page.locator('#period').inputValue(),'all');
  assert.equal(await page.locator('#source').getAttribute('aria-pressed'),'false');
  const metricFont = await page.locator('.metric-label span').first().evaluate(n=>getComputedStyle(n).fontSize);
  assert.ok(parseFloat(metricFont) >= 24, metricFont);
  await page.locator('#nav [data-view="trends"]').click();
  await page.locator('#content .heatmap').waitFor();
  await page.locator('#nav [data-view="songs"]').click();
  await page.locator('#content table').waitFor();
  await page.goBack();
  await page.locator('#content .heatmap').waitFor();
  assert.equal(await page.locator('#page-title').innerText(),'Listening trends');
  await page.goBack();
  await page.locator('#content .list-row').first().waitFor();
  assert.equal(await page.locator('#page-title').innerText(),'Overview');
  await page.locator('#content .list-row').first().click();
  await page.locator('#detail-dialog .calendar-heatmap').waitFor();
  await page.goBack();
  await page.waitForFunction(()=>!document.querySelector('#detail-dialog').open);
  assert.equal(await page.locator('#page-title').innerText(),'Overview');
  await page.goForward();
  await page.locator('#detail-dialog .calendar-heatmap').waitFor();
  await page.locator('#close-detail').click();
  await page.waitForFunction(()=>!document.querySelector('#detail-dialog').open);
  // Version display is a saved Settings preference, and merge names are choices.
  assert.equal(await page.locator('#toolbar [data-mode]').count(), 0);
  await page.locator('.settings-nav').click();
  await page.locator('#settings-preferences [data-mode="raw"]').click();
  assert.equal(await page.evaluate(() => localStorage.getItem('listening-version-mode')), 'raw');
  await page.goto((process.env.APP_URL || 'http://127.0.0.1:8105/') + '?demo=1');
  await page.locator('#content .metrics').waitFor();
  assert.equal(await page.locator('#settings-preferences [data-mode="raw"]').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#settings-preferences [data-mode="raw"]').isVisible(), false);
  await page.route('**/api/grouping-review?**', async route => {
    await route.fulfill({json:{rows:[{artist:'Fixture artist', ids:[501,502], key:'song:501:502',
      versions:[
        {id:1001,name:'Alpha [Mix]',plays:300,group_id:501},
        {id:1002,name:'Alpha',plays:3,group_id:501},
        {id:1003,name:'ALPHA',plays:40,group_id:502},
        {id:1004,name:'Alpha (Remaster)',plays:200,group_id:502}],
      reason:'Fixture choices', learnable:false}], total:1,offset:0,rules:[]}});
  });
  await page.locator('.settings-nav').click();
  const namesCard = page.locator('.review-card').first();
  await namesCard.locator('.candidate-version').nth(0).check();
  await namesCard.locator('.candidate-version').nth(1).check();
  assert.equal(await namesCard.locator('.candidate-name').inputValue(), 'Alpha');
  await namesCard.locator('.candidate-version').nth(2).check();
  await namesCard.locator('.candidate-version').nth(3).check();
  assert.equal(await namesCard.locator('.candidate-name').inputValue(), 'ALPHA');
  assert.equal(await namesCard.locator('.candidate-name-choice option').count(), 5);
  await namesCard.locator('.candidate-name-choice').selectOption('1002');
  assert.equal(await namesCard.locator('.candidate-name').inputValue(), 'Alpha');
  await namesCard.locator('.candidate-version').nth(1).uncheck();
  assert.equal(await namesCard.locator('.candidate-name').inputValue(), 'ALPHA');
  await namesCard.locator('.candidate-name').fill('My custom name');
  assert.equal(await namesCard.locator('.candidate-name-choice').inputValue(), 'custom');
  await namesCard.locator('.candidate-version').nth(3).uncheck();
  assert.equal(await namesCard.locator('.candidate-name').inputValue(), 'My custom name');
  await namesCard.locator('.candidate-name-choice').selectOption('1001');
  assert.equal(await namesCard.locator('.candidate-name').inputValue(), 'Alpha [Mix]');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.unroute('**/api/grouping-review?**');
  // A saved view stays usable while refreshing and updates without navigation.
  let cacheReady = false;
  await page.route("**/api/overview?**", async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.current.plays = cacheReady ? 11223 : 9876;
    data._cache = { stale: !cacheReady, refreshing: !cacheReady, generated: cacheReady ? 1791201600 : 1791115200, error: false };
    await route.fulfill({ response, json: data });
  });
  await page.locator('#nav [data-view="overview"]').click();
  await page.waitForFunction(() => document.querySelector('#content .metric-value')?.textContent === '9,876');
  assert.match(await page.locator('#cache-status').innerText(), /Saved view.*Updating/);
  assert.equal(await page.locator('#content').getAttribute('aria-busy'), 'false');
  await page.locator('#content [data-chart-zoom="in"]').click();
  const cachedZoom = await page.locator('#content .chart-scroll').getAttribute('data-zoom');
  cacheReady = true;
  await page.waitForFunction(() => document.querySelector('#content .metric-value')?.textContent === '11,223');
  assert.doesNotMatch(await page.locator('#cache-status').innerText(), /Updating/);
  assert.equal(await page.locator('#content .chart-scroll').getAttribute('data-zoom'), cachedZoom);
  await page.unroute("**/api/overview?**");
  // Detail totals drill into the selected entity and Back restores its dialog.
  await page.locator('#nav [data-view="overview"]').click();
  await page.locator('#content [data-detail="artist"][data-id="the beatles"]').click();
  await page.locator('#detail-dialog [data-browse="albums"]').waitFor();
  const artistSpotify = await page.locator('#detail-dialog .spotify-link').getAttribute('href');
  assert.match(decodeURIComponent(artistSpotify), /artist:"The Beatles"/);
  for (const [view, title] of [['albums', 'Albums'], ['songs', 'Songs'], ['history', 'Listening history'], ['artists', 'Artists']]) {
    await page.locator(`#detail-dialog [data-browse="${view}"]`).click();
    await page.locator('#content table').waitFor();
    assert.equal(await page.locator('#page-title').innerText(), title);
    assert.match(await page.locator('#content .filter-chip').innerText(), /The Beatles/);
    assert.equal(new URL(page.url()).searchParams.get('entity'), 'artist');
    assert.equal(new URL(page.url()).searchParams.get('id'), 'the beatles');
    assert.equal(new URL(page.url()).searchParams.get('period'), 'all');
    assert.doesNotMatch(await page.locator('#content tbody').innerText(), /Oasis|Radiohead/);
    await page.goBack();
    await page.locator('#detail-dialog [data-browse="albums"]').waitFor();
    assert.match(await page.locator('#detail-content h2').first().innerText(), /The Beatles/);
  }
  // An album's Songs total lists just that album's songs.
  await page.locator('#detail-dialog [data-browse="albums"]').click();
  await page.locator('#content [data-detail="album"]').first().click();
  await page.locator('#detail-dialog [data-browse="songs"]').waitFor();
  const albumSpotify = await page.locator('#detail-dialog .spotify-link').getAttribute('href');
  assert.match(decodeURIComponent(albumSpotify), /album:"Abbey Road".*artist:"The Beatles"/);
  const albumId = await page.locator('#detail-dialog [data-browse="songs"]').getAttribute('data-id');
  await page.locator('#detail-dialog [data-browse="songs"]').click();
  await page.locator('#content table').waitFor();
  assert.equal(new URL(page.url()).searchParams.get('entity'), 'album');
  assert.equal(new URL(page.url()).searchParams.get('id'), albumId);
  await page.locator('#clear-filter').click();
  await page.waitForFunction(() => !document.querySelector('#content .filter-chip'));
  assert.equal(new URL(page.url()).searchParams.has('entity'), false);
  // Metadata arrives later without discarding the chart or changing its zoom.
  await page.route('**/api/detail?*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.artwork = null; data.artwork_pending = true;
    await route.fulfill({response, json:data});
  });
  await page.route('**/api/artwork?*', route => route.fulfill({json:{artwork_pending:false,
    artwork:{url:'https://lastfm.freetls.fastly.net/i/u/174s/fixture.png',artist:'The Beatles',album:'Abbey Road'}}}));
  await page.route('https://lastfm.freetls.fastly.net/**', route => route.fulfill({path:path.join(__dirname, '../analytics/static/icon-192.png'),contentType:'image/png'}));
  await page.locator('#nav [data-view="overview"]').click();
  await page.locator('#content [data-detail="artist"][data-id="the beatles"]').click();
  await page.locator('#detail-dialog [data-chart-zoom="in"]').click();
  const artworkZoom = await page.locator('#detail-dialog .chart-scroll').getAttribute('data-zoom');
  await page.locator('#detail-artwork-slot img').waitFor();
  await page.waitForFunction(() => document.querySelector('#detail-artwork-slot img')?.naturalWidth > 0);
  assert.equal(await page.locator('#detail-dialog .chart-scroll').getAttribute('data-zoom'), artworkZoom);
  await page.setViewportSize({width:320,height:700});
  assert.equal(await page.locator('#detail-content').evaluate(el => el.scrollWidth <= el.clientWidth), true);
  await page.screenshot({path:path.join(output, 'artist-detail-artwork-mobile.png')});
  await page.unroute('**/api/detail?*');
  await page.unroute('**/api/artwork?*');
  await page.unroute('https://lastfm.freetls.fastly.net/**');
  // AudioDB's current R2 CDN is allowed, and a photo appears while album jobs remain pending.
  await page.locator('#close-detail').click();
  await page.waitForFunction(() => !document.querySelector('#detail-dialog').open);
  await page.route('**/api/detail?*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    Object.assign(data, {artwork:null, artist_photo:null, artist_logo:null, artwork_pending:true});
    await route.fulfill({response,json:data});
  });
  let partialPolls = 0;
  await page.route('**/api/artwork?*', route => {
    partialPolls++;
    return route.fulfill({json:{artwork:null, artwork_pending:true,
      artist_photo:'https://r2.theaudiodb.com/images/media/artist/thumb/beatles.jpg',
      artist_logo:null, artist_name:'The Beatles'}});
  });
  await page.route('https://r2.theaudiodb.com/**', route => route.fulfill({path:path.join(__dirname,'../analytics/static/icon-192.png'),contentType:'image/png'}));
  await page.locator('#content [data-detail="artist"][data-id="the beatles"]').click();
  await page.locator('#detail-artwork-slot img').waitFor();
  await page.waitForFunction(() => document.querySelector('#detail-artwork-slot img')?.naturalWidth > 0);
  assert.ok(partialPolls >= 1);
  assert.equal(await page.locator('#detail-artwork-slot img').getAttribute('src'), 'https://r2.theaudiodb.com/images/media/artist/thumb/beatles.jpg');
  assert.match(await page.locator('#detail-artwork-slot').innerText(), /TheAudioDB/);
  await page.screenshot({path:path.join(output,'audiodb-r2-photo-mobile.png')});
  await page.locator('#close-detail').click();
  await page.waitForFunction(() => !document.querySelector('#detail-dialog').open);
  await page.unroute('**/api/detail?*');
  await page.unroute('**/api/artwork?*');
  await page.unroute('https://r2.theaudiodb.com/**');
  assert.deepEqual(errors, []);
  console.log(
    "UI passed: setup, demo, dates, history, search, detail, separate, merge, undo, mobile, dark, heatmap and safe text rendering.",
  );
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
