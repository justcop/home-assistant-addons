// Optional browser regression suite: requires Playwright and Chromium.
// CHROMIUM_EXECUTABLE can select an existing local browser.
const { chromium } = require("playwright");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const assert = require("node:assert/strict");

(async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "money-browser-"));
  fs.writeFileSync(path.join(temp, "options.json"), JSON.stringify({web_password: "browser-fixture"}));
  const port = "18129";
  const server = spawn(
    process.env.PYTHON || "python",
    [path.join(__dirname, "browser_fixture_server.py")],
    {
      env: { ...process.env, MONEY_LOCAL: "1", MONEY_DATA: temp, PORT: port },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let browser;
  try {
    await new Promise((resolve, reject) => {
      server.stdout.once("data", resolve);
      server.once("error", reject);
      server.once("exit", (code) => reject(Error("Server exited: " + code)));
    });
    browser = await chromium.launch({
      executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
      headless: true,
      args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    });
    const page = await browser.newPage({
      viewport: { width: 1400, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("dialog", (d) => d.accept());
    const base = "http://127.0.0.1:" + port;
    await page.goto(base);
    await page.getByLabel("Password", {exact: true}).fill("wrong");
    await page.getByRole("button", {name: "Log in", exact: true}).click();
    await page.getByText("Incorrect password.", {exact: true}).waitFor();
    assert.equal((await page.request.get(base + "/api/export")).status(), 401);
    await page.getByLabel("Password", {exact: true}).fill("browser-fixture");
    await page.getByRole("button", {name: "Log in", exact: true}).click();
    await page
      .getByRole("heading", { name: "Welcome to Money Locations" })
      .waitFor();
    const accounts = [
      {
        id: "bank",
        name: "Example bank",
        type: "Cash",
        wrapper: "None",
        access: "Accessible",
      },
      {
        id: "sipp",
        name: "Example SIPP",
        type: "Stocks",
        wrapper: "SIPP",
        access: "Restricted",
      },
      {
        id: "mortgage",
        name: "Example mortgage",
        type: "Mortgage",
        wrapper: "None",
        access: "Liability",
      },
    ].map((a) => ({ ...a, holder: "Example", active: true, notes: "" }));
    const seed = {
      schema_version: 1,
      accounts,
      valuations: [],
      income_sources: ["Salary"],
      source_notes: [],
      snapshots: [
        {
          id: "opening",
          date: "2026-01-01",
          status: "final",
          activity_complete: true,
          required_accounts: accounts.map((a) => a.id),
          income: {},
          notes: "",
          balances: {
            bank: { amount: "10000", confirmed: true },
            sipp: { amount: "1000", confirmed: true },
            mortgage: { amount: "-5000", confirmed: true },
          },
        },
      ],
    };
    const file = path.join(temp, "example.json");
    fs.writeFileSync(file, JSON.stringify(seed));
    await page
      .getByRole("link", { name: "Import history", exact: true })
      .click();
    await page.locator("#import-file").setInputFiles(file);
    await page.locator("#import").click();
    await page.getByRole("heading", { name: "Your money, clearly." }).waitFor();
    assert.match(await page.locator("#view").innerText(), /£6,000.00/);
    assert.equal(await page.locator('.metric .positive').first().innerText(), '£6,000.00');
    await page.getByRole('link', {name: 'Backups & settings', exact: true}).click();
    await page.locator('#source-name').fill('Test source');
    await page.locator('#add-source').click();
    await page.getByRole('button', {name: 'Remove Test source income source', exact: true}).click();
    await page.getByRole('button', {name: 'Remove Test source income source', exact: true}).waitFor({state: 'hidden'});
    await page.getByRole('link', {name: 'Overview', exact: true}).click();
    await page.getByRole("link", { name: "New check-in" }).click();
    await page.locator("#new-date").fill("2026-02-01");
    await page.locator("#new-snapshot").click();
    await page.locator("#snapshot-form").waitFor();
    assert.equal(await page.locator('[data-income="Test source"]').count(), 0);
    await page.locator("#finalize").click();
    await page.locator("#error").waitFor({ state: "visible" });
    assert.match(
      await page.locator("#preview").innerText(),
      /balance is missing/,
    );
    for (const id of ["bank", "sipp", "mortgage"])
      await page.locator(`[data-unchanged="${id}"]`).click();
    assert.equal(await page.locator('[data-account="mortgage"][data-field="amount"]').evaluate(el => el.classList.contains('negative')), true);
    assert.equal(await page.locator('[data-account="bank"][data-field="amount"]').evaluate(el => el.classList.contains('positive')), true);
    await page
      .locator('[data-account="bank"][data-field="amount"]')
      .fill("11200");
    await page
      .locator('[data-account="sipp"][data-field="amount"]')
      .fill("2050");
    await page
      .locator('[data-account="sipp"][data-field="amount"]')
      .locator('xpath=ancestor::div[contains(@class,"account-entry")]')
      .locator("summary")
      .click();
    await page
      .locator('[data-account="sipp"][data-field="contribution"]')
      .fill("800");
    await page
      .locator('[data-account="sipp"][data-field="relief"]')
      .fill("200");
    await page.locator('[data-income="Salary"]').fill("3000");
    await page.locator('[data-root="activity_complete"]').check();
    await page.locator("#save-draft").click();
    await page.waitForFunction(
      () => document.querySelector("#save-status").textContent === "Saved",
    );
    const draftUrl = page.url();
    await page.reload();
    await page.locator("#snapshot-form").waitFor();
    assert.equal(
      await page
        .locator('[data-account="sipp"][data-field="amount"]')
        .inputValue(),
      "2050",
    );
    await page.locator("#finalize").click();
    await page.getByRole("heading", { name: "Period review" }).waitFor();
    const text = await page.locator("#view").innerText();
    assert.match(text, /£2,000.00/); // income retained
    assert.match(text, /£1,000.00/); // spending
    assert.match(text, /£50.00/); // genuine growth
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("link", { name: "Overview", exact: true }).click();
    await page.getByRole("heading", { name: "Your money, clearly." }).waitFor();
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    if (process.env.SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({
        path: path.join(process.env.SCREENSHOT_DIR, "money-mobile.png"),
        fullPage: true,
      });
      await page.setViewportSize({ width: 1400, height: 1000 });
      await page.screenshot({
        path: path.join(process.env.SCREENSHOT_DIR, "money-desktop.png"),
        fullPage: true,
      });
    }
    await page.getByRole("link", { name: "Backups & settings" }).click();
    await page.locator("#show-backups").click();
    await page.locator("#backups a").first().waitFor();
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "Complete backup (JSON)" }).click();
    const download = await downloadPromise;
    const exported = JSON.parse(fs.readFileSync(await download.path(), "utf8"));
    assert.equal(exported.snapshots.length, 2);
    assert.equal(exported.snapshots[1].status, "final");
    await page.locator("#valuation-date").fill("2026-01-01");
    await page.locator("#valuation-value").fill("100000");
    await page.locator("#add-valuation").click();
    await page.waitForFunction(() =>
      document.querySelector("#view").textContent.includes("£100,000.00"),
    );
    await page.getByRole("link", { name: "Overview", exact: true }).click();
    await page.locator("#home-toggle").check();
    assert.match(await page.locator("#view").innerText(), /£108,250.00/);
    // Explicit final-record edits, followed by the normal finalise path.
    await page.goto(draftUrl);
    await page.locator("#snapshot-form").waitFor();
    await page.locator('[data-income="Salary"]').fill("3100");
    await page.locator("#finalize").click();
    await page.getByRole("heading", { name: "Period review" }).waitFor();
    assert.match(await page.locator("#view").innerText(), /£1,100.00/);
    await page.getByRole("link", {name: "LifeStage & transactions", exact: true}).click();
    await page.getByRole("heading", {name: "LifeStage & transactions", exact: true}).waitFor();
    assert.equal(await page.locator("#hub-login").isVisible(), false);
    await page.getByRole("button", {name: "Connect LifeStage", exact: true}).click();
    await page.getByLabel("LifeStage verification code", {exact: true}).fill("123456");
    await page.waitForTimeout(2100); // User enters 2FA after the login throttle interval.
    await page.getByRole("button", {name: "Verify code", exact: true}).click();
    await page.getByRole("button", {name: "Disconnect", exact: true}).waitFor();
    await page.getByRole("button", {name: "Sync now", exact: true}).click();
    await page.getByText("Example purchase", {exact: true}).waitFor();
    await page.getByLabel("Map Example bank", {exact: true}).selectOption("bank");
    await page.getByRole("button", {name: "Save mapping", exact: true}).click();
    await page.getByRole("button", {name: "Create today’s draft from mapped balances", exact: true}).click();
    await page.locator("#snapshot-form").waitFor();
    assert.equal(await page.getByLabel("Example bank balance", {exact: true}).inputValue(), "123.45");
    assert.equal(await page.locator("#confirmed-bank").innerText(), "Needs confirmation");
    await page.getByRole("button", {name: "Confirm imported balance", exact: true}).click();
    await page.getByRole("link", {name: "LifeStage & transactions", exact: true}).click();
    await page.getByRole("button", {name: "Disconnect", exact: true}).click();
    await page.getByRole("button", {name: "Connect LifeStage", exact: true}).waitFor();
    assert.equal(await page.getByText("Example purchase", {exact: true}).count(),1);
    const allData=await (await page.request.get(base+"/api/export")).json();
    assert.equal(allData.moneyhub.transactions.length,1);
    assert.equal(allData.moneyhub.mappings[0].account_id,"bank");
    assert.equal(allData.snapshots.at(-1).balances.bank.confirmed,true);
    assert.equal(JSON.stringify(allData).includes("fixture-password"),false);
    await page.waitForTimeout(2100);
    await page.getByRole("button", {name: "Connect LifeStage", exact: true}).click();
    await page.getByLabel("LifeStage verification code", {exact: true}).waitFor();
    assert.equal(await page.getByLabel("LifeStage password", {exact: true}).inputValue(), "");
    await page.getByRole("button", {name: "Log out", exact: true}).click();
    await page.getByLabel("Password", {exact: true}).waitFor();
    assert.equal((await page.request.get(base + "/api/export")).status(), 401);
    assert.deepEqual(errors, []);
    console.log(
      "Browser checks passed: LifeStage 2FA, sync, mapping, confirmed draft and disconnect; password login/logout and export protection, import, missing-balance guard, unchanged confirmation, draft persistence, SIPP relief, finalisation, final edits, mobile width, backup/export and property toggle.",
    );
  } finally {
    if (browser) await browser.close();
    server.kill();
    fs.rmSync(temp, { recursive: true, force: true });
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
