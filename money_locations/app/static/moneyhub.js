"use strict";
let hubPoll = null;
let hubFilter = {q: "", account: "", offset: 0};
let hubDates = null;
const hubAmount = (value, currency) => value == null ? "No balance" : coloured(value, `${esc(currency || "Unknown currency")} ${Number(value).toLocaleString("en-GB", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`);

function hubStatus(s) {
  return `<p role="status">${s.job.running ? esc(s.job.message) : s.connected ? "LifeStage session saved" : "Reconnect to fetch new data"}. ${s.transaction_count} stored transactions across ${s.account_count} source accounts.</p>
    ${s.last_sync ? `<p class="muted subtle">Last successful pull: ${esc(new Date(s.last_sync).toLocaleString("en-GB"))}. ${s.last_range ? `Requested transactions: ${esc(s.last_range.start)} to ${esc(s.last_range.end)}.` : ""}</p>` : ""}
    ${s.job.error ? `<p role="alert" class="negative">${esc(s.job.error)}</p>` : !s.job.running && s.job.message ? `<p>${esc(s.job.message)}</p>` : ""}`;
}

async function moneyhubPage() {
  clearTimeout(hubPoll);
  const [status, sources, transactions] = await Promise.all([
    api("moneyhub/status"), api("moneyhub/accounts"),
    api("moneyhub/transactions?"+new URLSearchParams(hubFilter))
  ]);
  if (!hubDates) {
    const start=new Date(today()+"T12:00:00Z");
    start.setUTCDate(start.getUTCDate()-90);
    hubDates={start:start.toISOString().slice(0,10),end:today()};
  }
  return heading("LifeStage & transactions", "Pull your Moneyhub / WPS LifeStage accounts and transactions into Money Locations.") +
  `<div id="moneyhub-view" data-saved-login="${status.saved_login ? "yes" : "no"}">
  <section class="panel"><h2>Connection</h2><div id="hub-status">${hubStatus(status)}</div>
  ${status.connected ? `<div class="actions"><button data-hub="reauth">Reauthenticate</button><button data-hub="disconnect">Disconnect</button></div>` : ""}
  ${status.saved_login && !status.connected && !status.needs_code ? `<p><button data-hub="saved-login" class="primary">Connect LifeStage</button></p>` : ""}
  <form id="hub-login" ${status.saved_login || status.connected || status.needs_code ? "hidden" : ""}><div class="form-grid">
    ${input("LifeStage email", 'id="hub-email" type="email" autocomplete="username" required')}
    ${input("LifeStage password", 'id="hub-password" type="password" autocomplete="current-password" required')}
    </div><p><button class="primary" type="submit">Connect LifeStage</button></p><p class="muted subtle">Details entered here are used for this login only. Alternatively, save lifestage_email and lifestage_password in the add-on configuration. The app keeps the resulting session and asks for a verification code when required.</p></form>
  <form id="hub-verify" ${status.needs_code ? "" : "hidden"}><label>LifeStage verification code<input id="hub-code" autocomplete="one-time-code" inputmode="numeric" pattern="[0-9]{4,10}" required></label><p><button type="submit" class="primary">Verify code</button> <button type="button" data-hub="reauth">Start login again</button></p></form>
  </section>
  <section class="panel"><h2>Pull data</h2><form id="hub-sync"><div class="form-grid">${input("Transactions from", 'id="hub-start" type="date" required', hubDates.start)}${input("Transactions to", 'id="hub-end" type="date" required',hubDates.end)}</div><p><button type="submit" class="primary" ${!status.connected || status.job.running ? "disabled" : ""}>Sync now</button></p></form>
  <p class="muted subtle">Balances are the latest available values. The date range applies to transactions. Repeat pulls update matching transaction IDs. Transfers, pending transactions and foreign currencies are retained for review. Provider history may be incomplete; the app does not claim a full bank statement.</p>
  <label class="hub-checkbox"><input id="hub-background" type="checkbox" ${status.background ? "checked" : ""}> Sync daily while the session remains valid</label><p class="muted subtle">Daily sync revisits the last successful sync date with a seven-day overlap. Choose an older range above to refresh earlier history. If LifeStage expires the session, reconnect here.</p>
  </section>
  <section class="panel"><h2>Map accounts</h2><p>Match each source account to an existing Money Locations account. Create missing accounts in Accounts first. Mapping does not change recorded history.</p>
  <div class="table-wrap"><table><thead><tr><th>LifeStage account</th><th>Latest balance</th><th>Money Locations account</th><th>Balance sign</th><th></th></tr></thead><tbody>
  ${sources.map(a=>`<tr data-source="${esc(a.uid)}"><td>${esc(a.name)}<small>${esc(a.bank)} · ${esc(a.type)}${a.closed ? " · closed / disconnected" : ""}</small></td><td>${hubAmount(a.balance,a.currency)}<small>${esc(a.balance_date || "Date unknown")}</small></td><td><select aria-label="Map ${esc(a.name)}" class="hub-map"><option value="">Unmapped</option>${state.accounts.map(local=>`<option value="${esc(local.id)}" ${a.mapping?.account_id===local.id ? "selected" : ""}>${esc(local.name)}${local.active ? "" : " (inactive)"}</option>`).join("")}</select></td><td><select class="hub-sign" aria-label="Balance sign for ${esc(a.name)}"><option value="1" ${a.mapping?.multiplier!==-1 ? "selected" : ""}>As supplied</option><option value="-1" ${a.mapping?.multiplier===-1 ? "selected" : ""}>Reverse sign</option></select></td><td><button data-hub="map">Save mapping</button></td></tr>`).join("") || '<tr><td colspan="5">Connect and sync to see source accounts.</td></tr>'}
  </tbody></table></div><p class="muted subtle">Mortgages and other debts should be negative in Money Locations. Check the supplied sign before reversing it. Property accounts stay separate from financial balances.</p>
  <button data-hub="draft" ${sources.some(a=>a.mapping) ? "" : "disabled"}>Create today’s draft from mapped balances</button><p class="muted subtle">Uses mapped, open GBP accounts only. Every imported balance needs confirmation. Income, contributions, withdrawals, relief and mortgage interest still need completing. Transactions do not automatically feed the savings calculation.</p>
  </section>
  <section class="panel"><h2>Transactions</h2><form id="hub-filter" class="actions">${input("Search description or merchant", 'id="hub-search" type="search"',hubFilter.q)}<label>Source account<select id="hub-account"><option value="">All accounts</option>${sources.map(a=>`<option value="${esc(a.uid)}" ${hubFilter.account===a.uid ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select></label><button type="submit">Search</button></form>
  <p>${transactions.total ? `${transactions.offset+1}–${transactions.offset+transactions.items.length} of ${transactions.total}` : "No matching transactions"}</p>
  <div class="table-wrap"><table><thead><tr><th>Date</th><th>Account</th><th>Description</th><th>Amount</th><th>Status</th></tr></thead><tbody>${transactions.items.map(t=>`<tr><td>${esc(t.date)}</td><td>${esc(t.account_name)}</td><td>${esc(t.description)}${t.merchant ? `<small>${esc(t.merchant)}</small>` : ""}</td><td class="num">${hubAmount(t.amount,t.currency)}</td><td>${esc(t.status)}</td></tr>`).join("")}</tbody></table></div>
  <div class="actions"><button data-hub="previous" ${transactions.offset===0 ? "disabled" : ""}>Previous</button><button data-hub="next" ${transactions.offset+transactions.items.length>=transactions.total ? "disabled" : ""}>Next</button></div><p class="muted subtle">The complete JSON backup in Backups & settings includes imported records and mappings, without login credentials.</p>
  </section></div>`;
}

function bindMoneyhub() {
  const container=$("#moneyhub-view");
  if (!container) return;
  // Keep the existing app's generic button handler away from these forms.
  container.addEventListener("click", e => e.stopPropagation());
  container.addEventListener("submit", async e => {
    e.preventDefault();
    e.stopPropagation();
    const button=e.target.querySelector('button[type="submit"]');
    button.disabled=true;
    clearError();
    try {
      switch(e.target.id) {
        case "hub-login": {
          const password=$("#hub-password").value;
          $("#hub-password").value="";
          const result=await api("moneyhub/login",{email:$("#hub-email").value,password});
          if (result.needs_code) {
            $("#hub-login").hidden=true;
            $("#hub-verify").hidden=false;
            $("#hub-code").focus();
          } else await route();
          break;
        }
        case "hub-verify": {
          const code=$("#hub-code").value;
          $("#hub-code").value="";
          await api("moneyhub/verify",{code});
          await route();
          break;
        }
        case "hub-sync":
          hubDates={start:$("#hub-start").value,end:$("#hub-end").value};
          await api("moneyhub/sync",hubDates);
          await route();
          break;
        case "hub-filter":
          hubFilter={q:$("#hub-search").value,account:$("#hub-account").value,offset:0};
          await route();
          break;
      }
    } catch(e) {error(e);} finally {button.disabled=false;}
  });
  container.addEventListener("click", async e => {
    const button=e.target.closest("[data-hub]");
    if (!button) return;
    button.disabled=true;
    clearError();
    try {
      switch(button.dataset.hub) {
        case "reauth":
          if (container.dataset.savedLogin !== "yes") {
            $("#hub-login").hidden=false;$("#hub-verify").hidden=true;$("#hub-email").focus();break;
          }
          // Saved settings always supply the login; only the code needs input.
        case "saved-login": {
          const result=await api("moneyhub/login",{use_saved:true});
          if (result.needs_code) {
            $("#hub-login").hidden=true;$("#hub-verify").hidden=false;$("#hub-code").focus();
          } else await route();
          break;
        }

        case "disconnect":
          await api("moneyhub/disconnect",{});await route();break;
        case "map": {
          const row=button.closest("tr");
          await api("moneyhub/map",{uid:row.dataset.source,account_id:row.querySelector(".hub-map").value,multiplier:Number(row.querySelector(".hub-sign").value)});
          await route();break;
        }
        case "draft": {
          const d=await api("moneyhub/draft",{revision});
          revision=d.revision;location.hash="edit/"+d.result;break;
        }
        case "previous":hubFilter.offset=Math.max(0,hubFilter.offset-100);await route();break;
        case "next":hubFilter.offset+=100;await route();break;
      }
    } catch(e) {error(e);} finally {button.disabled=false;}
  });
  $("#hub-background").addEventListener("change",async e=>{
    try {await api("moneyhub/settings",{background:e.target.checked});}
    catch(err) {e.target.checked=!e.target.checked;error(err);}
  });
  const poll=async()=>{
    if (location.hash!=="#moneyhub" || !$("#login").hidden) return;
    try {
      const s=await api("moneyhub/status");
      if (!container.isConnected) return;
      $("#hub-status").innerHTML=hubStatus(s);
      if (s.job.running) hubPoll=setTimeout(poll,1500);
      else if (container.dataset.syncRunning==="yes") await route();
    } catch(e) {if(container.isConnected) error(e);}
  };
  api("moneyhub/status").then(s=>{
    if (!container.isConnected) return;
    if (s.job.running) {container.dataset.syncRunning="yes";hubPoll=setTimeout(poll,1000);}
  }).catch(error);
}
