"use strict";
let state,
  revision,
  reports,
  editing = null,
  timer = null,
  saveChain = Promise.resolve(),
  dirty = false,
  includeHome = false;
const $ = (s) => document.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const gbp = (v) =>
  v === null || v === undefined
    ? "Not available"
    : new Intl.NumberFormat("en-GB", {
        style: "currency",
        currency: "GBP",
      }).format(v);
const day = (v) =>
  v
    ? new Date(v + "T12:00:00").toLocaleDateString("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : "Opening snapshot";
const today = () =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/London",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
const clone = (v) => JSON.parse(JSON.stringify(v));
const signed = (v) =>
  `<span class="${v < 0 ? "negative" : "positive"}">${gbp(v)}</span>`;
const pct = (v) =>
  v === null || v === undefined ? "—" : `${Number(v).toFixed(2)}%`;
function error(e) {
  $("#error").hidden = false;
  $("#error").textContent = e.message || String(e);
}
function clearError() {
  $("#error").hidden = true;
}
async function api(path, body) {
  const r = await fetch(
    "api/" + path,
    body
      ? {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Money-Request": "1",
          },
          body: JSON.stringify(body),
        }
      : {},
  );
  const data = await r.json();
  if (r.status === 401 && path !== "auth/login") showLogin();
  if (!r.ok) throw Error(data.error || "Request failed");
  return data;
}
async function load() {
  const d = await api("state");
  ({ state, revision, reports } = d);
}
async function action(action, body = {}) {
  const d = await api("action", { action, revision, ...body });
  revision = d.revision;
  return d.result;
}
function badge(r) {
  if (r.legacy) return '<span class="badge warn">Provisional</span>';
  if (r.warnings?.length) return '<span class="badge warn">Check</span>';
  return '<span class="badge">Complete</span>';
}
function heading(title, subtitle, buttons = "") {
  return `<div class="title-row"><div><h1>${title}</h1><p class="muted">${subtitle}</p></div><div class="actions">${buttons}</div></div>`;
}
function chart(data, key) {
  if (data.length < 2)
    return '<p class="muted">Your trend will appear after two snapshots.</p>';
  const vals = data.map((r) => r[key]).filter((v) => v !== null);
  if (!vals.length)
    return "<p>Add a dated home valuation to see this view.</p>";
  let min = Math.min(...vals),
    max = Math.max(...vals);
  if (max === min) {
    max += 1;
    min -= 1;
  }
  const start = new Date(data[0].date).getTime(),
    end = new Date(data.at(-1).date).getTime();
  const pts = data
    .filter((r) => r[key] !== null)
    .map((r) => [
      64 + ((new Date(r.date).getTime() - start) / (end - start)) * 576,
      175 - ((r[key] - min) / (max - min)) * 150,
    ]);
  const poly = pts.map((p) => p.join(",")).join(" ");
  return `<svg class="chart" viewBox="0 0 660 210" role="img" aria-label="Net financial assets over time"><line class="grid" x1="64" y1="25" x2="640" y2="25"/><line class="grid" x1="64" y1="175" x2="640" y2="175"/><text x="0" y="29">£${Math.round(max / 1000)}k</text><text x="0" y="179">£${Math.round(min / 1000)}k</text><polygon class="area" points="${pts[0][0]},175 ${poly} ${pts.at(-1)[0]},175"/><polyline class="line" points="${poly}"/><text x="64" y="205">${esc(day(data[0].date))}</text><text x="640" y="205" text-anchor="end">${esc(day(data.at(-1).date))}</text></svg>`;
}
function metric(label, value, sub = "", hero = false) {
  return `<div class="card ${hero ? "hero" : ""}"><span class="muted">${label}</span><strong class="metric">${gbp(value)}</strong><small class="muted">${sub}</small></div>`;
}
function breakdown(r) {
  if (!r.previous_date)
    return '<p class="muted">This opening snapshot establishes your starting balances.</p>';
  return `<div class="breakdown"><div><span>Inferred savings from ordinary income</span><strong>${gbp(r.savings)}</strong></div><div><span>Investment return after mortgage cost</span><strong>${gbp(r.net_return)}</strong></div><div><span>Pension relief & account bonuses</span><strong>${gbp(r.tax_benefits)}</strong></div><div><span>Other capital changes</span><strong>${gbp(r.capital_change)}</strong></div><div class="total"><span>Change in net financial assets</span><strong>${gbp(r.balance_change)}</strong></div></div>`;
}
function longerView() {
  const periods = reports.filter((r) => r.previous_date);
  if (!periods.length) return "";
  const recent = periods.slice(-3);
  const sum = (rows, key) => rows.reduce((total, r) => total + Number(r[key] || 0), 0);
  const totalDays = sum(recent, "days");
  const per30 = (key) => totalDays ? (sum(recent, key) * 30) / totalDays : null;
  return `<section class="panel"><h2>The longer view</h2><p class="muted subtle">Rates are normalised to 30 days so uneven snapshot intervals are comparable. These use the latest ${recent.length} intervals. Imported estimates remain provisional.</p><div class="cards">${metric("Average inferred savings / 30 days", per30("savings"))}${metric("Average net return / 30 days", per30("net_return"))}${metric("Cumulative inferred savings", sum(periods, "savings"), "Since " + day(reports[0].date))}${metric("Cumulative net return", sum(periods, "net_return"), "Since " + day(reports[0].date))}</div></section>`;
}
function overview() {
  const r = reports.at(-1);
  if (!r)
    return (
      heading(
        "Your money, in one place.",
        "Start with your existing history or create your first snapshot.",
      ) +
      `<div class="panel empty"><h2>Welcome to Money Locations</h2><p class="muted">Import your prepared history file to pick up where your spreadsheet left off.</p><a class="button primary" href="#settings">Import history</a> <a class="button" href="#accounts">Add accounts</a></div>`
    );
  return (
    heading(
      "Your money, clearly.",
      `Latest snapshot · ${day(r.date)}`,
      `<a class="button primary" href="#checkin">New check-in</a>`,
    ) +
    `<div class="section-row">${badge(r)}<label class="toggle"><input type="checkbox" id="home-toggle" ${includeHome ? "checked" : ""}>Include home value</label></div><div class="cards">${metric(includeHome ? "Total net worth including home" : "Net financial assets", includeHome ? r.with_home : r.net_worth, includeHome ? (r.home_value_date ? "Home valued " + day(r.home_value_date) : "Add a valuation in settings") : "Includes mortgage; excludes property value", true)}${metric("Inferred savings", r.savings, "Includes mortgage principal")}${metric("Net investment return", r.net_return, "After allocated mortgage interest")}${metric("Inferred spending", r.adjusted_spending, "After excluded payments")}</div>${r.warnings.length ? `<div class="notice"><strong>Worth checking</strong><ul>${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}<div class="grid-two"><section class="panel"><div class="section-row"><h2>How your position has changed</h2></div>${chart(reports, includeHome ? "with_home" : "net_worth")}</section><section class="panel"><h2>This period, explained</h2><p class="muted subtle">${day(r.previous_date)} to ${day(r.date)} · ${r.days ?? 0} days</p>${breakdown(r)}<p class="subtle"><button class="history-button" data-report="${r.id}">See the full calculation</button></p></section></div><div class="grid-two"><section class="panel"><h2>Where your money is</h2><div class="breakdown">${Object.entries(
      r.totals,
    )
      .filter(([k, v]) => v)
      .map(
        ([k, v]) =>
          `<div><span>${esc(k)}</span><strong>${gbp(v)}</strong></div>`,
      )
      .join(
        "",
      )}</div></section><section class="panel"><h2>Useful totals</h2><div class="breakdown"><div><span>Accessible & withdrawal-dependent assets</span><strong>${gbp(r.accessible)}</strong></div><div><span>ISA & Lifetime ISA balances</span><strong>${gbp(r.isa_total)}</strong></div><div><span>Restricted pensions & LISA</span><strong>${gbp(r.restricted)}</strong></div><div><span>Mortgage principal saved this period</span><strong>${gbp(r.mortgage_principal)}</strong></div><div><span>Other savings</span><strong>${gbp(r.other_savings)}</strong></div></div><p class="muted subtle">Accessibility follows your account classifications. Some investments require notice or repayment before withdrawal.</p></section></div>${longerView()}`
  );
}
function detailed(r) {
  return (
    heading(
      "Period review",
      `${day(r.previous_date)} to ${day(r.date)}`,
      `<button data-edit="${r.id}">Edit figures</button><button data-reopen="${r.id}">Reopen as draft</button><a class="button" href="#history">All history</a>`,
    ) +
    `${badge(r)}${r.warnings.map((w) => `<div class="notice">${esc(w)}</div>`).join("")}<div class="cards">${metric("Net financial assets", r.net_worth, "Includes mortgage; excludes property value", true)}${metric("Inferred savings", r.savings)}${metric("Net investment return", r.net_return)}${metric("Inferred spending", r.adjusted_spending)}</div><div class="grid-two"><section class="panel"><h2>Period breakdown</h2>${breakdown(r)}<p class="muted subtle">Savings is inferred from the balance movement after investment return, tax benefits and exceptional capital changes. This is an accounting breakdown, not an independent reconciliation check.</p></section><section class="panel"><h2>Income and spending</h2><div class="breakdown">${Object.entries(
      state.snapshots.find((s) => s.id === r.id)?.income || {},
    )
      .map(
        ([k, v]) =>
          `<div><span>${esc(k)}</span><strong>${v === "" || v === null ? "Unrecorded" : gbp(Number(v))}</strong></div>`,
      )
      .join(
        "",
      )}<div class="total"><span>Income total</span><strong>${gbp(r.income)}</strong></div><div><span>Excluded payments</span><strong>${gbp(r.excluded_payments)}</strong></div><div><span>Adjusted income</span><strong>${gbp(r.adjusted_income)}</strong></div><div><span>Less inferred savings</span><strong>${gbp(r.savings)}</strong></div><div class="total"><span>Inferred adjusted spending</span><strong>${gbp(r.adjusted_spending)}</strong></div></div></section></div><section class="panel"><h2>Investment returns</h2><p class="muted subtle">Mortgage interest is allocated by closing stocks and P2P balances. Percentage return is a simple comparison with the opening balance, useful as a sanity check rather than a money-weighted performance measure.</p><div class="table-wrap"><table><thead><tr><th>Type</th><th class="num">Balance</th><th class="num">Gross return</th><th class="num">Gross %</th><th class="num">Mortgage cost</th><th class="num">Net return</th></tr></thead><tbody>${Object.entries(
      r.groups,
    )
      .map(
        ([k, g]) =>
          `<tr><td>${k}</td><td class="num">${gbp(g.balance)}</td><td class="num">${r.previous_date ? gbp(g.gross) : "—"}</td><td class="num">${pct(g.return_pct)}</td><td class="num">${gbp(g.cost)}</td><td class="num">${r.previous_date ? signed(g.net) : "—"}</td></tr>`,
      )
      .join(
        "",
      )}</tbody></table></div>${r.unallocated_cost ? `<p>Unallocated mortgage cost: ${gbp(r.unallocated_cost)}</p>` : ""}</section><section class="panel"><h2>Account detail</h2>${r.legacy ? '<p class="muted">Historical contributions were recorded by category. Individual account returns are unavailable.</p>' : ""}<div class="table-wrap"><table><thead><tr><th>Account</th><th class="num">Previous</th><th class="num">Balance</th><th class="num">Change</th><th class="num">Gross %</th><th class="num">Net return</th></tr></thead><tbody>${r.accounts
      .filter((a) => a.balance !== null || a.previous)
      .map(
        (a) =>
          `<tr><td>${esc(a.name)}<br><small>${esc(a.type)} · ${esc(a.wrapper)}</small></td><td class="num">${gbp(a.previous)}</td><td class="num">${a.balance === null ? "Unrecorded" : gbp(a.balance)}</td><td class="num">${gbp(a.change)}</td><td class="num">${pct(a.return_pct)}</td><td class="num">${a.net === null ? "—" : signed(a.net)}</td></tr>`,
      )
      .join(
        "",
      )}</tbody></table></div></section>${r.home_value !== null ? `<section class="panel"><h2>Home value</h2><p>${gbp(r.home_value)} as at ${day(r.home_value_date)}. Total net worth including home: <strong>${gbp(r.with_home)}</strong>.</p><p class="muted">${r.property_change === null ? "No comparable opening valuation." : `Valuation change this period: ${gbp(r.property_change)}. This is separate from savings.`}</p></section>` : ""}`
  );
}
function history() {
  return (
    heading(
      "Your financial history",
      "Every snapshot, with 30-day-normalised period figures for fair comparison.",
    ) +
    `<section class="panel"><div class="table-wrap"><table><thead><tr><th>Snapshot</th><th>Status</th><th class="num">Net financial assets</th><th class="num">Days</th><th class="num">Savings / 30d</th><th class="num">Net return / 30d</th><th class="num">Spending / 30d</th></tr></thead><tbody>${[
      ...state.snapshots,
    ]
      .sort((a, b) => b.date.localeCompare(a.date))
      .map((s) => {
        const r = reports.find((r) => r.id === s.id);
        return `<tr><td><button class="history-button" ${r ? "data-report" : "data-edit"}="${s.id}">${day(s.date)}</button></td><td>${r ? badge(r) : '<span class="badge">Draft</span>'}</td><td class="num">${r ? gbp(r.net_worth) : "—"}</td><td class="num">${r?.days ?? "—"}</td><td class="num">${r ? gbp(r.savings_30d) : "—"}</td><td class="num">${r ? gbp(r.net_return_30d) : "—"}</td><td class="num">${r ? gbp(r.adjusted_spending_30d) : "—"}</td></tr>`;
      })
      .join("")}</tbody></table></div></section>`
  );
}
function input(label, attrs, value = "", help = "") {
  return `<label>${label}<input ${attrs} value="${esc(value)}">${help ? `<small>${help}</small>` : ""}</label>`;
}
function checkin() {
  const drafts = state.snapshots.filter((s) => s.status === "draft");
  return (
    heading(
      "A moment for your money.",
      "Record balances and activity since your previous snapshot.",
    ) +
    `<section class="panel"><h2>Start a check-in</h2><div class="actions">${input("Snapshot date", 'id="new-date" type="date"', today())}<button class="primary" id="new-snapshot">Start check-in</button></div><p class="muted subtle">Use negative balances for mortgages and credit-card debts. All activity covers the interval between snapshots.</p></section>${drafts.length ? `<section class="panel"><h2>Continue a draft</h2>${drafts.map((s) => `<p><button data-edit="${s.id}">${day(s.date)}</button></p>`).join("")}</section>` : ""}`
  );
}
function editForm(s) {
  const prev = reports.filter((r) => r.date < s.date).at(-1);
  const previous = state.snapshots.find((x) => x.id === prev?.id);
  return (
    heading(
      "Monthly check-in",
      prev
        ? `Activity since ${day(prev.date)}. Drafts save automatically.`
        : "Your opening snapshot establishes the baseline.",
    ) +
    `<form id="snapshot-form"><section class="panel"><div class="form-grid">${input("Snapshot date", `type="date" data-root="date" ${s.status === "final" ? "disabled" : ""}`, s.date)}<label>Record status<input disabled value="${s.status === "final" ? "Final snapshot · edits require Save changes" : "Draft · saved automatically"}"></label></div></section><section class="panel"><div class="section-row"><div><h2>1. Account balances</h2><p class="muted subtle">Enter a current figure or explicitly confirm an unchanged balance.</p></div></div>${state.accounts
      .filter((a) => a.id in s.balances || s.required_accounts.includes(a.id))
      .map((a) => {
        const b = s.balances[a.id] || {};
        const before = previous?.balances[a.id]?.amount;
        return `<div class="account-entry"><div class="account-top"><div class="account-name">${esc(a.name)}<small>${esc(a.type)} · ${esc(a.wrapper)} ${!a.active ? "· inactive" : ""}</small></div><div class="previous"><small>Previous balance</small>${before === null || before === undefined || before === "" ? "Unrecorded" : gbp(Number(before))}</div><label>New balance<input type="number" step="0.01" data-account="${a.id}" data-field="amount" value="${esc(b.amount)}" aria-label="${esc(a.name)} balance"><small id="confirmed-${a.id}">${b.confirmed ? "✓ Confirmed" : "Needs confirmation"}</small></label><button type="button" class="small" data-unchanged="${a.id}" ${before === null || before === undefined || before === "" ? "disabled" : ""}>Unchanged</button></div><details class="flows"><summary>Contributions, relief and adjustments</summary><div class="form-grid">${([
          "Stocks",
          "P2P",
          "Crypto",
        ].includes(a.type)
          ? [
              ["contribution", "Own-money contributions"],
              ["withdrawal", "Withdrawals"],
            ]
          : []
        )
          .concat(
            a.wrapper === "SIPP" || a.wrapper === "Lifetime ISA"
              ? [
                  [
                    "relief",
                    a.wrapper === "SIPP"
                      ? "Relief added to SIPP"
                      : "LISA bonus received",
                  ],
                ]
              : [],
          )
          .concat(a.type === "Cash" ? [["interest", "Interest credited"]] : [])
          .concat([["capital", "Other capital adjustment (+/−)"]])
          .map(([f, l]) =>
            input(
              l,
              `type="number" step="0.01" ${f === "capital" ? "" : 'min="0"'} data-account="${a.id}" data-field="${f}"`,
              b[f],
            ),
          )
          .join(
            "",
          )}</div><p class="muted subtle">Contributions exclude relief and bonuses. Transfers between investment accounts need a withdrawal on one and a contribution on the other. Capital adjustments identify exceptional changes, such as a write-off or new mortgage borrowing.</p></details></div>`;
      })
      .join(
        "",
      )}</section><section class="panel"><h2>2. Income received</h2><p class="muted subtle">Net income received during this period, excluding pension tax refunds or an identified pension benefit in your tax code.</p><div class="form-grid">${[...new Set([...state.income_sources, ...Object.keys(s.income)])].map((k) => input(esc(k), `type="number" step="0.01" data-income="${esc(k)}"`, s.income[k])).join("")}</div></section><section class="panel"><h2>3. Adjustments</h2><div class="form-grid">${input("Mortgage interest cost", 'type="number" min="0" step="0.01" data-root="mortgage_interest"', s.mortgage_interest, "Enter a positive cost. Allocated across stocks and P2P, excluded from spending.")}${input("Additional pension tax benefit received", 'type="number" min="0" step="0.01" data-root="pension_refund"', s.pension_refund, "HMRC refund or identified tax-code benefit. Exclude this amount from ordinary income above.")}${input("Excluded payments", 'type="number" min="0" step="0.01" data-root="excluded_payments"', s.excluded_payments, "Reduces income and spending equally, for example a separate NHS pension payment.")}${input("Other overall capital change (+/−)", 'type="number" step="0.01" data-root="capital_change"', s.capital_change, "Gifts or adjustments not already entered against an account. Do not enter ordinary transfers.")}</div><details class="flows"><summary>Help calculate mortgage interest</summary><p class="subtle">For a straightforward repayment period with no new borrowing or fees: payments minus reduction in mortgage debt.</p><div class="actions">${input("Total mortgage payments", 'type="number" min="0" step="0.01" id="mortgage-payments"')}<button type="button" id="calculate-interest">Calculate</button></div></details><p class="notice">Record SIPP top-ups against the SIPP account above, once they first enter its tracked balance. If the displayed balance already includes pending relief, do not record it again on settlement. Expected relief outside your balance is not included.</p></section>${s.legacy ? `<section class="panel"><h2>Imported category contributions</h2><p class="muted">These historical figures take precedence over account-level contributions for this period. Correct them here if needed.</p><div class="form-grid">${["Stocks", "P2P", "Crypto"].map((g) => input(g + " net contribution", `type="number" step="0.01" data-legacy="${g}"`, s.legacy.group_contributions[g])).join("")}${input("Cash interest", 'type="number" step="0.01" min="0" data-legacy-cash="1"', s.legacy.cash_interest)}</div><p class="subtle muted">Original transfer adjustment retained for reference: ${gbp(Number(s.legacy.transfer_adjustment || 0))}. It is not automatically classified as income or growth.</p></section>` : ""}<section class="panel"><h2>Notes and review</h2><label>Period notes<textarea data-root="notes">${esc(s.notes)}</textarea></label><label class="checkline"><input type="checkbox" data-root="activity_complete" ${s.activity_complete ? "checked" : ""}>I have included all income, contributions, withdrawals, interest, relief and adjustments. Blank activity fields mean zero.</label><div id="preview"></div></section><div class="sticky-actions"><button type="button" id="preview-button">Review period</button>${s.status === "draft" ? '<button type="button" id="save-draft">Save draft</button>' : ""}<button type="button" class="primary" id="finalize">${s.status === "final" ? "Save changes" : "Finalise snapshot"}</button>${s.status === "draft" ? '<button type="button" id="delete-draft">Discard draft</button>' : ""}<span class="muted subtle" id="form-status"></span></div></form>`
  );
}
function accounts() {
  return (
    heading(
      "Your accounts",
      "Keep closed accounts for history. Switch them off for future check-ins.",
      `<button id="add-account" class="primary">Add account</button>`,
    ) +
    `<section class="panel"><div class="table-wrap"><table class="account-list"><thead><tr><th>Account</th><th>Holder</th><th>Type</th><th>Wrapper</th><th>Active</th><th></th></tr></thead><tbody>${state.accounts.map((a) => `<tr><td>${esc(a.name)}</td><td>${esc(a.holder)}</td><td>${esc(a.type)}</td><td>${esc(a.wrapper)}</td><td>${a.active ? "Yes" : "No"}</td><td><button class="small" data-account-edit="${a.id}">Edit</button></td></tr>`).join("")}</tbody></table></div></section><div id="account-editor"></div>`
  );
}
function settings() {
  return (
    heading(
      "Backups & settings",
      "Your data stays here. Export a complete copy whenever you need it.",
    ) +
    `<section class="panel"><h2>Import or restore</h2><p>Select a Money Locations JSON file. Importing replaces the current dataset and saves a recovery copy first.</p><input type="file" id="import-file" accept=".json,application/json"><div class="actions">${state.snapshots.length ? input("Type RESTORE to replace current data", 'id="restore-confirm"') : ""}<button id="import" class="primary">${state.snapshots.length ? "Restore file" : "Import history"}</button></div></section><section class="panel"><h2>Export your data</h2><div class="actions"><a class="button primary" href="api/export" download>Complete backup (JSON)</a><a class="button" href="api/csv" download>Balance history (CSV)</a><button id="show-backups">Recovery copies</button></div><p class="muted subtle">The JSON export includes all accounts, balances, activity, draft snapshots, notes and valuations. CSV is a balance-and-flow table for spreadsheets. Data survives reinstalls in /share/money_locations. Include the share folder in Home Assistant backups.</p><div id="backups"></div></section><section class="panel"><h2>Home valuations</h2><p class="muted">Enter your share of the property value. The latest valuation on or before each snapshot is used.</p><div class="form-grid">${input("Valuation date", 'id="valuation-date" type="date"', today())}${input("Value of your share (£)", 'id="valuation-value" type="number" min="0" step="0.01"')}${input("Notes", 'id="valuation-notes"')}</div><p><button id="add-valuation">Save valuation</button></p>${state.valuations
      .sort((a, b) => b.date.localeCompare(a.date))
      .map(
        (v) =>
          `<p>${day(v.date)} · <strong>${gbp(Number(v.value))}</strong> · ${esc(v.notes)}</p>`,
      )
      .join(
        "",
      )}</section><section class="panel"><h2>Income sources</h2><p>${state.income_sources.map(esc).join(" · ")}</p><div class="actions">${input("New source", 'id="source-name"')}<button id="add-source">Add source</button></div></section><section class="panel explanation"><h2>How the figures work</h2><p>Investment growth is the change in account balance after removing contributions, adding back withdrawals, and removing relief and capital adjustments. Cash interest is entered explicitly.</p><p>Mortgage interest reduces investment returns in proportion to closing stocks and P2P balances. Net financial assets include the mortgage as a negative balance while excluding the property value unless you explicitly include home. Savings are inferred from the change in net financial assets less net investment return, tax benefits and exceptional capital changes. Mortgage principal repayment is part of savings.</p><p>Inferred spending is ordinary income less inferred savings. Excluded payments reduce both displayed income and spending equally. Home revaluation is shown separately.</p><p>Historical blanks remain unrecorded. They contribute zero to legacy totals, matching the source spreadsheet. Older returns are estimates where contribution, interest or relief records are incomplete.</p>${state.source_notes.map((n) => `<p class="muted subtle">${esc(n)}</p>`).join("")}</section>`
  );
}
async function saveEdit(finalize = false) {
  clearTimeout(timer);
  const payload = clone(editing);
  if (!payload) return;
  payload.status = finalize ? "final" : payload.status;
  const task = async () => {
    const id = await action("snapshot", { snapshot: payload });
    state.snapshots = state.snapshots.map((s) =>
      s.id === id ? clone(payload) : s,
    );
    if (JSON.stringify(editing) === JSON.stringify(payload) || finalize)
      dirty = false;
    $("#save-status").textContent = finalize ? "Snapshot finalised" : "Saved";
    return id;
  };
  saveChain = saveChain.then(task);
  try {
    return await saveChain;
  } catch (e) {
    $("#save-status").textContent = "Not saved";
    error(e);
    saveChain = Promise.resolve();
    throw e;
  }
}
async function preview() {
  const r = await api("preview", { snapshot: editing });
  $("#preview").innerHTML =
    `<h3>Period breakdown</h3>${breakdown(r)}<p>Inferred spending: <strong>${gbp(r.adjusted_spending)}</strong></p>${r.warnings.length ? `<div class="notice"><strong>Worth checking</strong><ul>${r.warnings.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}${r.issues.length ? `<div class="notice"><strong>Before finalising</strong><ul>${r.issues.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : '<p class="notice good">All required balances are confirmed and activity is marked complete.</p>'}`;
  return r;
}
function markDirty() {
  dirty = true;
  $("#save-status").textContent =
    editing.status === "draft" ? "Unsaved changes…" : "Unsaved changes";
  clearTimeout(timer);
  if (editing.status === "draft")
    timer = setTimeout(() => saveEdit().catch(() => {}), 900);
}
function bindForm() {
  const form = $("#snapshot-form");
  if (!form) return;
  form.addEventListener("submit", (e) => e.preventDefault());
  form.addEventListener("input", (e) => {
    const el = e.target;
    if (el.dataset.account) {
      const b = (editing.balances[el.dataset.account] ??= {});
      b[el.dataset.field] = el.value;
      if (el.dataset.field === "amount") {
        b.confirmed = el.value !== "";
        $("#confirmed-" + el.dataset.account).textContent = b.confirmed
          ? "✓ Confirmed"
          : "Needs confirmation";
      }
    } else if (el.dataset.root) {
      editing[el.dataset.root] = el.type === "checkbox" ? el.checked : el.value;
    } else if (el.dataset.income) {
      editing.income[el.dataset.income] = el.value;
    } else if (el.dataset.legacy) {
      editing.legacy.group_contributions[el.dataset.legacy] = el.value;
    } else if (el.dataset.legacyCash) {
      editing.legacy.cash_interest = el.value;
    } else return;
    markDirty();
  });
}
async function route() {
  try {
    if (editing && dirty) {
      if (editing.status === "draft") await saveEdit();
      else if (
        !confirm("Leave this final snapshot without saving your edits?")
      ) {
        return;
      }
    }
    clearTimeout(timer);
    await saveChain;
    editing = null;
    dirty = false;
    clearError();
    await load();
    const parts = location.hash.slice(1).split("/");
    const page = parts[0] || "overview";
    document
      .querySelectorAll("nav a")
      .forEach((a) => a.classList.toggle("active", a.hash === "#" + page));
    let html;
    if (page === "edit") {
      editing = clone(state.snapshots.find((s) => s.id === parts[1]));
      if (!editing) throw Error("Snapshot not found.");
      html = editForm(editing);
    } else if (page === "report") {
      const r = reports.find((r) => r.id === parts[1]);
      html = r ? detailed(r) : history();
    } else
      html = (
        { overview, checkin, history, accounts, settings }[page] || overview
      )();
    $("#view").innerHTML = html;
    bindForm();
  } catch (e) {
    error(e);
  }
}
function accountEditor(id) {
  const a = state.accounts.find((a) => a.id === id) || {
    name: "",
    type: "Cash",
    wrapper: "None",
    access: "Accessible",
    holder: "Me",
    active: true,
    notes: "",
  };
  const select = (label, key, options) =>
    `<label>${label}<select id="a-${key}">${options.map((v) => `<option ${v === a[key] ? "selected" : ""}>${v}</option>`).join("")}</select></label>`;
  $("#account-editor").innerHTML =
    `<section class="panel"><h2>${id ? "Edit" : "Add"} account</h2><div class="form-grid">${input("Account name", 'id="a-name"', a.name)}${input("Account holder", 'id="a-holder"', a.holder)}${select("Asset type", "type", ["Stocks", "P2P", "Cash", "Crypto", "Mortgage", "Credit card", "Tax liability", "Receivable"])}${select("Wrapper", "wrapper", ["None", "ISA", "SIPP", "Lifetime ISA"])}${select("Access", "access", ["Accessible", "Withdrawal dependent", "Restricted", "Repayment dependent", "Liability"])}<label>Active<input type="checkbox" id="a-active" ${a.active ? "checked" : ""}></label></div>${input("Notes", 'id="a-notes"', a.notes)}<p><button id="save-account" data-id="${id || ""}" class="primary">Save account</button></p><p class="muted subtle">Type, wrapper and access are locked once an account has history, to preserve past calculations. Newly activated accounts appear in newly created check-ins.</p></section>`;
  $("#account-editor").scrollIntoView({ behavior: "smooth" });
}
document.addEventListener("change", (e) => {
  if (e.target.id === "home-toggle") {
    includeHome = e.target.checked;
    $("#view").innerHTML = overview();
  }
});
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  try {
    clearError();
    if (b.dataset.report) {
      location.hash = "report/" + b.dataset.report;
      return;
    }
    if (b.dataset.edit) {
      location.hash = "edit/" + b.dataset.edit;
      return;
    }
    if (b.dataset.reopen) {
      if (confirm("Reopen this snapshot and any later final snapshots as drafts? Their balances and activity will be preserved, but affected periods must be reviewed and finalised again.")) {
        await action("reopen_snapshot", { id: b.dataset.reopen });
        location.hash = "edit/" + b.dataset.reopen;
      }
      return;
    }
    if (b.dataset.accountEdit) {
      accountEditor(b.dataset.accountEdit);
      return;
    }
    if (b.dataset.unchanged) {
      const aid = b.dataset.unchanged;
      const prev = state.snapshots
        .filter((s) => s.status === "final" && s.date < editing.date)
        .sort((a, b) => a.date.localeCompare(b.date))
        .at(-1);
      const value = prev?.balances[aid]?.amount;
      if (value === undefined || value === null || value === "")
        throw Error("No previous balance available.");
      editing.balances[aid].amount = value;
      editing.balances[aid].confirmed = true;
      document.querySelector(
        `[data-account="${aid}"][data-field="amount"]`,
      ).value = value;
      $("#confirmed-" + aid).textContent = "✓ Confirmed unchanged";
      markDirty();
      return;
    }
    b.disabled = true;
    switch (b.id) {
      case "new-snapshot": {
        const id = await action("new", { date: $("#new-date").value });
        location.hash = "edit/" + id;
        break;
      }
      case "preview-button":
        await preview();
        break;
      case "save-draft":
        editing.status = "draft";
        await saveEdit();
        break;
      case "finalize": {
        await saveChain;
        const r = await preview();
        if (r.issues.length)
          throw Error("Complete the items in the review before finalising.");
        const id = await saveEdit(true);
        editing = null;
        location.hash = "report/" + id;
        break;
      }
      case "delete-draft":
        if (confirm("Discard this draft?")) {
          clearTimeout(timer);
          await saveChain;
          await action("delete_draft", { id: editing.id });
          dirty = false;
          editing = null;
          location.hash = "history";
        }
        break;
      case "calculate-interest": {
        const prior = state.snapshots
          .filter((s) => s.status === "final" && s.date < editing.date)
          .sort((a, b) => a.date.localeCompare(b.date))
          .at(-1);
        if (!prior) throw Error("An opening snapshot is needed first.");
        const ids = state.accounts
          .filter((a) => a.type === "Mortgage")
          .map((a) => a.id);
        if (
          !ids.length ||
          ids.some(
            (id) =>
              editing.balances[id]?.amount === "" ||
              editing.balances[id]?.amount == null ||
              prior.balances[id]?.amount === "" ||
              prior.balances[id]?.amount == null,
          )
        )
          throw Error("Enter opening and closing mortgage balances first.");
        const payments = $("#mortgage-payments").value;
        if (payments === "") throw Error("Enter mortgage payments.");
        const principal = ids.reduce(
          (sum, id) =>
            sum +
            Number(editing.balances[id].amount) -
            Number(prior.balances[id].amount),
          0,
        );
        const interest = Number(payments) - principal;
        if (interest < 0)
          throw Error(
            "Calculated interest is negative. Check payments, balances, borrowing and fees.",
          );
        editing.mortgage_interest = interest.toFixed(2);
        $('[data-root="mortgage_interest"]').value = editing.mortgage_interest;
        markDirty();
        break;
      }
      case "add-account":
        accountEditor();
        break;
      case "save-account": {
        const a = {
          id: b.dataset.id,
          name: $("#a-name").value,
          holder: $("#a-holder").value,
          type: $("#a-type").value,
          wrapper: $("#a-wrapper").value,
          access: $("#a-access").value,
          active: $("#a-active").checked,
          notes: $("#a-notes").value,
        };
        await action("account", { account: a });
        await route();
        break;
      }
      case "add-source":
        await action("income_source", { name: $("#source-name").value });
        await route();
        break;
      case "add-valuation":
        await action("valuation", {
          date: $("#valuation-date").value,
          value: $("#valuation-value").value,
          notes: $("#valuation-notes").value,
        });
        await route();
        break;
      case "import": {
        const file = $("#import-file").files[0];
        if (!file) throw Error("Select a JSON export first.");
        const data = JSON.parse(await file.text());
        await action("import", {
          state: data,
          confirm: $("#restore-confirm")?.value,
        });
        location.hash = "overview";
        break;
      }
      case "show-backups": {
        const backups = await api("backups");
        $("#backups").innerHTML = backups.length
          ? `<p class="muted">Download a recovery copy, then use Restore file above.</p>${backups.map((v) => `<p><a href="api/backup?id=${v.id}" download>${esc(new Date(v.created).toLocaleString("en-GB"))}</a> · before ${esc(v.reason)}</p>`).join("")}`
          : "<p>No recovery copies yet.</p>";
        break;
      }
    }
  } catch (err) {
    error(err);
  } finally {
    b.disabled = false;
  }
});
window.addEventListener("hashchange", () => { if (state && $("#login").hidden) route(); });
window.addEventListener("beforeunload", (e) => {
  if (dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
function showLogin(configured = true) {
  document.querySelector('.shell').hidden = true;
  $('#login').hidden = false;
  $('#login-form').hidden = !configured;
  $('#login-note').textContent = configured ? 'Log in to your financial picture.' : 'Set web_password in the Home Assistant add-on configuration, then restart the add-on.';
  clearTimeout(timer);
  $('#view').replaceChildren();
}
async function start() {
  try {
    const auth = await api('auth/status');
    if (!auth.authenticated) return showLogin(auth.configured);
    $('#login').hidden = true;
    document.querySelector('.shell').hidden = false;
    await route();
  } catch (e) { error(e); }
}
$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const button = e.target.querySelector('button');
  button.disabled = true;
  $('#login-error').textContent = '';
  try {
    await api('auth/login', {password: $('#password').value});
    $('#password').value = '';
    await start();
  } catch (e) { $('#login-error').textContent = e.message; }
  finally { button.disabled = false; }
});
$('#logout').addEventListener('click', async () => {
  try {
    if (editing && dirty && editing.status === "draft") await saveEdit();
    await saveChain;
    if (dirty) { error(Error('Save your changes before logging out.')); return; }
    await api('auth/logout', {});
    showLogin();
  } catch (e) { error(e); }
});
start();
