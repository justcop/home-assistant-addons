"use strict";
const $ = (s) => document.querySelector(s);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const number = (n) => new Intl.NumberFormat("en-GB").format(n ?? 0);
const initial = new URLSearchParams(location.search);
const views = [
  "overview",
  "trends",
  "artists",
  "albums",
  "songs",
  "history",
  "settings",
];
const state = {
  view: views.includes(initial.get("view")) ? initial.get("view") : "overview",
  period: initial.get("period") || "30d",
  source: ["vinyl", "unknown"].includes(initial.get("source")) ? initial.get("source") : "all",
  mode: initial.get("mode") === "raw" ? "raw" : "merged",
  start: initial.get("start") || "",
  end: initial.get("end") || "",
  demo: initial.get("demo") === "1",
  q: "",
  offset: 0,
  filter: null,
  groupKind: "song",
  selected: new Map(),
};
let statusData,
  requestController,
  loadSerial = 0,
  detailSerial = 0,
  toastTimer,
  searchTimer;
const titles = {
  overview: ["Overview", "Understand the patterns behind your plays."],
  trends: ["Listening trends", "See how your listening changes over time."],
  artists: ["Artists", "The artists shaping your listening."],
  albums: ["Albums", "A clearer view across album editions."],
  songs: ["Songs", "Your real favourites, with versions brought together."],
  history: [
    "Listening history",
    "Explore the individual plays behind the numbers.",
  ],
  settings: [
    "Settings & grouping",
    "Your connection, data quality and version decisions.",
  ],
};
let theme;
try {
  theme = localStorage.getItem("listening-theme");
} catch (_) {}
document.documentElement.dataset.theme =
  theme ||
  (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
function formatDate(
  ts,
  options = { day: "numeric", month: "short", year: "numeric" },
) {
  return ts
    ? new Intl.DateTimeFormat("en-GB", {
        ...options,
        timeZone: statusData?.timezone || "Europe/London",
      }).format(new Date(ts * 1000))
    : "—";
}
function ago(ts) {
  if (!ts) return "Not synced yet";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  return seconds < 60
    ? "Synced just now"
    : seconds < 3600
      ? `Synced ${Math.floor(seconds / 60)}m ago`
      : `Synced ${Math.floor(seconds / 3600)}h ago`;
}
function query(extra = {}) {
  const p = {
    period: state.period,
    source: state.source,
    mode: state.mode,
    start: state.start,
    end: state.end,
    ...(state.filter ? { entity: state.filter.kind, id: state.filter.id } : {}),
    ...extra,
  };
  if (state.demo) p.demo = "1";
  for (const k of Object.keys(p))
    if (p[k] === null || p[k] === undefined || p[k] === "") delete p[k];
  return new URLSearchParams(p);
}
async function api(path, extra = {}, opts = {}) {
  const response = await fetch(`api/${path}?${query(extra)}`, {
    ...opts,
    headers: {
      ...(opts.body
        ? {
            "Content-Type": "application/json",
            "X-CSRF-Token": $("meta[name=csrf-token]").content,
          }
        : {}),
      ...opts.headers,
    },
  });
  let data;
  try {
    data = await response.json();
  } catch (_) {
    throw new Error(
      `The server returned ${response.status}. Please reload the page.`,
    );
  }
  if (!response.ok)
    throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function toast(text) {
  clearTimeout(toastTimer);
  $("#toast").textContent = text;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 5000);
}
function report(error) {
  if (error.name === "AbortError") return;
  $("#error").textContent = error.message;
  $("#error").hidden = false;
}
function urlState() {
  const q = query({ view: state.view, entity: null, id: null });
  history.replaceState(null, "", `${location.pathname}?${q}`);
}
function changeView(view) {
  state.view = view;
  state.q = "";
  state.offset = 0;
  state.filter = null;
  state.selected.clear();
  load();
}
function difference(current, previous, compare = true) {
  if (!compare || previous === null || previous === undefined)
    return '<span class="metric-foot">In selected period</span>';
  if (!previous)
    return `<span class="change">${current ? "New in period" : "No change"}</span>`;
  const pct = ((current - previous) / previous) * 100;
  return `<span class="change">${pct > 0 ? "+" : ""}${pct.toFixed(1)}%</span><span class="metric-foot">vs previous period</span>`;
}
function metrics(data, mode = state.mode) {
  return `<div class="metrics">${[
    ["plays", "Scrobbles", "↗"],
    ["artists", "Artists", "◎"],
    ["albums", "Albums", "▣"],
    ["songs", mode === "raw" ? "Song entries" : "Songs", "♫"],
  ]
    .map(
      ([k, label, icon]) =>
        `<div class="metric"><div class="metric-label">${label}<span aria-hidden="true">${icon}</span></div><div class="metric-value">${number(data.current[k])}</div><div>${difference(data.current[k], data.previous?.[k], data.period.compare)}</div></div>`,
    )
    .join("")}</div>`;
}
function panelHead(title, sub, action = "") {
  return `<div class="panel-head"><div><h2>${title}</h2>${sub ? `<p>${sub}</p>` : ""}</div>${action}</div>`;
}
function chart(data, filter = null, mode = state.mode) {
  const bins = data.timeline,
    max = Math.max(4, ...bins.map((b) => b.plays));
  const rounded = Math.ceil(max / 4) * 4;
  const best = bins.reduce((a, b) => (b.plays > a.plays ? b : a), {
    plays: 0,
    label: "",
  });
  const ds = filter
    ? ` data-entity="${esc(filter.kind)}" data-id="${esc(filter.id)}" data-name="${esc(filter.name)}" data-history-mode="${mode}"`
    : "";
  return `<div class="chart-layout"><div class="chart-axis"><span>${number(rounded)}</span><span>${number(rounded * 0.75)}</span><span>${number(rounded * 0.5)}</span><span>${number(rounded * 0.25)}</span><span>0</span></div><div class="bar-chart" role="group" aria-label="Scrobbles by ${bins[0]?.label.length === 7 ? "month" : "day"}, select a bar to inspect history">${bins.map((b) => `<button data-chart-start="${b.start}" data-chart-end="${b.end}"${ds} style="height:${Math.max(0.5, (b.plays / rounded) * 100)}%" title="${b.label}: ${number(b.plays)} scrobbles. Open history." aria-label="${b.label}, ${number(b.plays)} scrobbles"></button>`).join("")}</div></div><div class="chart-labels"><span>${esc(bins[0]?.label)}</span><span>${esc(bins[Math.floor(bins.length / 2)]?.label)}</span><span>${esc(bins.at(-1)?.label)}</span></div><div class="chart-foot"><span><strong>${number(data.current.plays)}</strong> scrobbles in this period</span><span>${best.plays ? `Peak: <strong>${esc(best.label)}</strong>` : "No plays yet"}</span></div>`;
}
function detailAttrs(kind, row) {
  return `data-detail="${kind}" data-id="${esc(row.id)}"`;
}
function topList(title, kind, rows, view) {
  return `<section class="panel">${panelHead(title, "", `<button class="button quiet" data-view="${view}">View all ↗</button>`)}<div>${
    rows.length
      ? rows
          .map(
            (r, i) =>
              `<button class="list-row" ${detailAttrs(kind, r)}><span class="rank">${i + 1}</span><span class="art-tile" aria-hidden="true">${esc(
                (r.artist || r.name)
                  .split(/\s+/)
                  .slice(0, 2)
                  .map((s) => s[0])
                  .join(""),
              )}</span><span class="list-name"><strong>${esc(r.name)}</strong><small>${esc(r.artist || "Artist")}${r.versions > 1 ? ` · ${r.versions} versions` : ""}</small></span><span class="list-count">${number(r.plays)}</span></button>`,
          )
          .join("")
      : '<div class="empty">No listening in this period.</div>'
  }</div></section>`;
}
function discovery(data) {
  const d = data.discovery;
  const pct =
    data.current.plays && d.complete
      ? Math.round((d.plays / data.current.plays) * 100)
      : 0;
  return `<section class="panel">${panelHead("Discovery & familiarity", "Artists first heard in your imported history")}<div class="insight-number">${d.complete ? `${pct}<span style="font-size:21px">%</span>` : "—"}</div><p style="font-size:12px">${d.complete ? "of plays were artists new to this period" : "Waiting for your complete history"}</p><div class="split-bar" role="img" aria-label="${pct}% new artist plays"><span style="width:${pct}%"></span></div><div class="legend"><span>New ${d.complete ? number(d.plays) : "—"}</span><span>Familiar ${d.complete ? number(data.current.plays - d.plays) : "—"}</span></div><div class="insight-note">${d.complete ? `<strong>${number(d.artists)} artists</strong> first appear in this period. “New” refers to your available Last.fm history, not necessarily the first time you ever heard them.` : "This measure needs the full import so older favourites are not mistaken for discoveries."}</div></section>`;
}
function insightsStrip(data) {
  const g = data.grouping,
    saved = g.raw_songs - g.songs;
  const r = data.returning[0];
  return `<div class="insight-strip"><article class="insight-card"><span class="insight-icon" aria-hidden="true">⇄</span><div><h3>One song, a clearer total</h3><p>${number(g.raw_songs)} song entries become ${number(g.songs)} groups. ${saved ? `${number(saved)} split entries are brought together.` : "Explore grouping to bring related versions together."} <button class="text-button" data-view="settings">Review groups →</button></p></div></article><article class="insight-card"><span class="insight-icon" aria-hidden="true">↺</span><div><h3>${r ? "Back in rotation" : "Returning favourites"}</h3><p>${r ? `<button class="text-button" ${detailAttrs("artist", r)}>${esc(r.name)}</button> returned after ${number(Math.floor(r.gap_days))} days without a recorded play, with ${number(r.plays)} plays this period.` : "Artists returning after at least 90 days without a recorded play will appear here."}${!data.discovery.complete ? " This is based on the history imported so far." : ""}</p></div></article></div>`;
}
function heatmap(data) {
  const max = Math.max(1, ...data.hours.flat());
  return `<section class="panel">${panelHead("When you listen", `Scrobbles by local weekday and hour · ${esc(data.period.timezone)}`)}<div class="heatmap"><span></span>${Array.from({ length: 24 }, (_, i) => `<span class="hour">${i % 3 === 0 ? String(i).padStart(2, "0") : ""}</span>`).join("")}${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day, d) => `<span class="day">${day}</span>${data.hours[d].map((count, h) => `<span class="cell" role="img" style="opacity:${0.07 + (0.93 * count) / max}" title="${day} ${String(h).padStart(2, "0")}:00: ${number(count)} scrobbles" aria-label="${day} ${h}:00, ${number(count)} scrobbles"></span>`).join("")}`).join("")}</div><p class="method-note">Repeated daylight-saving hours share a cell. These are scrobble timestamps, not measured listening duration.</p></section>`;
}
function overviewHTML(data) {
  return `${metrics(data)}<div class="grid-two"><section class="panel">${panelHead("Listening activity", `${data.period.start_label} to ${data.period.end_label}`, '<span class="pill">Scrobbles</span>')}${chart(data)}</section>${discovery(data)}</div>${insightsStrip(data)}<div class="grid-equal">${topList("Top artists", "artist", data.top_artists, "artists")}${topList("Top albums", "album", data.top_albums, "albums")}${topList("Top songs", "song", data.top_songs, "songs")}</div>`;
}
function trendsHTML(data) {
  return `${metrics(data)}<section class="panel" style="margin-bottom:22px">${panelHead("Your listening over time", "Select any bar to explore the plays behind it.")}${chart(data)}</section><div class="grid-two">${heatmap(data)}<section class="panel">${panelHead("Artist concentration", "Share of plays from your five most played artists")}<div class="insight-number">${data.top_five_share}%</div><p class="method-note">${number(data.current.artists)} artists in this period. A higher share means more listening centred on your favourites.</p><div class="split-bar"><span style="width:${data.top_five_share}%"></span></div><div class="legend"><span>Top five</span><span>Other artists</span></div></section></div><div class="grid-two">${discovery(data)}<section class="panel">${panelHead("Rediscoveries", "At least 90 days since the previous recorded play")}${data.returning.map((r) => `<button class="list-row" ${detailAttrs("artist", r)}><span class="list-name"><strong>${esc(r.name)}</strong><small>${number(Math.floor(r.gap_days))} days between plays</small></span><span class="list-count">${number(r.plays)}</span></button>`).join("") || '<p class="method-note">No rediscoveries in this period.</p>'}</section></div>`;
}
function pager(total) {
  return `<div class="pager"><span>${total ? `${number(state.offset + 1)}–${number(Math.min(state.offset + 50, total))} of ${number(total)}` : "No results"}</span><div><button class="button" data-page="prev" ${state.offset === 0 ? "disabled" : ""}>Previous</button><button class="button" data-page="next" ${state.offset + 50 >= total ? "disabled" : ""}>Next</button></div></div>`;
}
function rankTable(data, kind, settings = false) {
  const max = data.rows[0]?.plays || 1;
  return `<section class="panel table-panel"><div class="table-head"><div><h2>${settings ? "Canonical groups" : `Ranked ${state.view}`}</h2><small>${settings ? "All time. Select groups by the same artist to combine." : `${number(data.total)} results · ${state.mode === "raw" ? "Original Last.fm names" : "Combined versions"}`}</small></div><input id="search" aria-label="Search ${settings ? "groups" : state.view}" type="search" placeholder="Search ${settings ? "groups" : state.view}…" value="${esc(state.q)}"></div>${settings ? `<div class="selection-bar"><select id="group-kind" aria-label="Grouping type"><option value="song" ${kind === "song" ? "selected" : ""}>Songs</option><option value="album" ${kind === "album" ? "selected" : ""}>Albums</option></select><span id="selected-count">${state.selected.size} selected</span><button class="button primary" id="merge" ${state.selected.size < 2 ? "disabled" : ""}>Merge selected</button></div>` : ""}<div class="table-wrap"><table><thead><tr><th>${settings ? "Select" : "#"}</th><th>${kind === "artist" ? "Artist" : kind === "song" ? "Song / artist" : "Album / artist"}</th><th class="num">Plays</th><th class="num">${settings ? "Versions" : "Previous"}</th><th class="num">${settings ? "" : "Change"}</th></tr></thead><tbody>${data.rows.map((r, i) => `<tr><td>${settings ? `<input type="checkbox" class="group-select" data-id="${r.id}" data-name="${esc(r.name)}" ${state.selected.has(String(r.id)) ? "checked" : ""} aria-label="Select ${esc(r.name)} by ${esc(r.artist)}">` : state.offset + i + 1}</td><td class="name-cell"><button class="text-button" ${detailAttrs(kind, r)} ${settings ? 'data-group-detail="true"' : ""}><strong>${esc(r.name)}</strong></button><small>${esc(r.artist)}${kind !== "artist" && r.versions > 1 ? ` · ${number(r.versions)} versions` : ""}</small></td><td class="num"><strong>${number(r.plays)}</strong><div class="row-progress"><i style="width:${(r.plays / max) * 100}%"></i></div></td><td class="num">${settings ? number(r.versions) : r.previous === null ? "—" : number(r.previous)}</td><td class="num">${settings ? "" : r.previous === null ? "—" : !r.previous ? '<span class="change">New</span>' : `<span class="change">${r.plays >= r.previous ? "+" : ""}${(((r.plays - r.previous) / r.previous) * 100).toFixed(1)}%</span>`}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">No results for this search or date range.</td></tr>'}</tbody></table></div>${pager(data.total)}</section>`;
}
function historyHTML(data) {
  let lastDay = "";
  const rows = data.rows
    .map((r) => {
      const day = r.local_time.slice(0, 10);
      const head =
        day !== lastDay
          ? `<tr class="history-day"><td colspan="4">${formatDate(r.ts, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</td></tr>`
          : "";
      lastDay = day;
      return (
        head +
        `<tr><td>${formatDate(r.ts, { hour: "2-digit", minute: "2-digit", timeZoneName: "short" })}</td><td class="name-cell"><button class="text-button" data-detail="song" data-id="${state.mode === "raw" ? r.song_id : r.song_group}"><strong>${esc(state.mode === "raw" ? r.title : r.song_name)}</strong></button>${r.title !== r.song_name && state.mode !== "raw" ? `<small>Scrobbled as ${esc(r.title)}</small>` : ""}</td><td><button class="text-button" data-detail="artist" data-id="${esc(r.artist_key)}">${esc(r.artist)}</button>${r.source === "vinyl" ? "<small>Vinyl</small>" : ""}</td><td>${r.album_id ? `<button class="text-button" data-detail="album" data-id="${state.mode === "raw" ? r.album_id : r.album_group}">${esc(state.mode === "raw" ? r.album : r.album_name)}</button>` : "<small>No album supplied</small>"}</td></tr>`
      );
    })
    .join("");
  return `${state.filter ? `<div class="filter-chip">${esc(state.filter.name)}<button class="icon-button" id="clear-filter" aria-label="Clear history filter">×</button></div>` : ""}<section class="panel table-panel"><div class="table-head"><div><h2>Your listening diary</h2><small>${number(data.total)} scrobbles · ${esc(statusData.timezone)}</small></div><input id="search" type="search" aria-label="Search history" placeholder="Search songs, artists or albums…" value="${esc(state.q)}"></div><div class="table-wrap"><table><thead><tr><th>Time</th><th>Song</th><th>Artist</th><th>Album</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="empty">No scrobbles match this selection.</td></tr>'}</tbody></table></div>${pager(data.total)}</section>`;
}
function settingsHTML(data) {
  const c = statusData.counts,
    imp = statusData.import_state;
  return `<div class="settings-grid"><section class="panel">${panelHead("Last.fm connection", "Configured in the Home Assistant add-on options")}<dl><dt>Account</dt><dd>${esc(statusData.username || "Not configured")}</dd><dt>API key</dt><dd>${statusData.configured ? "Configured, hidden" : "Not configured"}</dd><dt>Vinyl connection</dt><dd>${statusData.source_reporting_enabled ? "Enabled" : "Not configured"}</dd><dt>Vinyl reports received</dt><dd>${number(statusData.source_reports)}</dd><dt>Sync interval</dt><dd>${statusData.interval} seconds</dd><dt>Daily reconciliation</dt><dd>Last ${statusData.reconcile_days} days</dd><dt>Display timezone</dt><dd>${esc(statusData.timezone)}</dd><dt>History import</dt><dd>${imp.complete ? "Complete" : imp.cursor ? "In progress" : "Not started"}</dd></dl><p class="method-note">Set your username and API key in the add-on’s Configuration tab, then restart. Changing account opens a separate database. <a href="https://www.last.fm/api/account/create" target="_blank" rel="noopener noreferrer">Get an API key ↗</a></p></section><section class="panel">${panelHead("Data quality", "What your statistics are built on")}<dl><dt>Active scrobbles</dt><dd>${number(c.plays)}</dd><dt>Earliest play</dt><dd>${formatDate(c.earliest)}</dd><dt>Missing album metadata</dt><dd>${number(c.missing_albums)}</dd><dt>Removed remotely, archived</dt><dd>${number(statusData.removed)}</dd></dl><p class="method-note">Counts are recorded scrobbles. Actual listening time and full-album sessions are not inferred. Album groups are scoped to the scrobbled track artist, so compilation albums may appear under several artists.</p></section></div><section class="panel" style="margin-bottom:22px">${panelHead("Version decisions", "Each change is local and reversible.", `<button class="button" id="undo" ${statusData.events.some((e) => !e.undone) ? "" : "disabled"}>Undo latest change</button>`)}<p class="method-note">Recognised remaster suffixes combine automatically. Live recordings, acoustic performances and mixes retain their labels. Select groups below to merge them, or open a group to separate a version. The first selected group supplies the display name. Artist totals are unaffected.</p>${
    statusData.events.length
      ? `<div style="margin-top:16px">${statusData.events
          .slice(0, 4)
          .map(
            (e) =>
              `<p style="font-size:11px;margin-top:6px">${esc(e.description)} · ${formatDate(e.ts)} ${e.undone ? "· Undone" : ""}</p>`,
          )
          .join("")}</div>`
      : ""
  }</section>${rankTable(data, state.groupKind, true)}`;
}
function setupHTML() {
  return `<section class="panel setup"><div class="setup-mark" aria-hidden="true">◫</div><h2>Your listening, ready to explore</h2><p>Connect Last.fm to bring your listening history together. Explore trends, find returning favourites and combine versions into meaningful totals.</p><ol><li>Open this add-on’s <strong>Configuration</strong> tab in Home Assistant.</li><li>Enter your <strong>Last.fm username</strong> and <a href="https://www.last.fm/api/account/create" target="_blank" rel="noopener noreferrer">API key</a>.</li><li>Save, restart the add-on and reopen this dashboard.</li></ol><p>The first import runs in the background and resumes after restarts. Your Last.fm history is never edited.</p><a class="button primary" href="?demo=1">Explore a fictional demo ↗</a></section>`;
}
async function fetchStatus() {
  statusData = await api("status");
  $("#listener").textContent = statusData.username || "Your library";
  $("#connection").textContent = statusData.demo
    ? "Fictional demo"
    : statusData.configured
      ? "Connected to Last.fm"
      : "Not connected";
  $("#refresh").disabled = statusData.demo || !statusData.configured;
  $("#sync-label").textContent = statusData.demo
    ? "Demo data"
    : statusData.sync.phase === "importing"
      ? "Importing history…"
      : statusData.sync.phase === "syncing"
        ? "Syncing…"
        : statusData.sync.phase === "error"
          ? "Sync needs attention"
          : ago(statusData.last_sync);
  $("#footer-note").textContent =
    `${number(statusData.counts.plays)} scrobbles · ${statusData.timezone}`;
  const imp = statusData.import_state;
  let banner = "";
  if (statusData.demo)
    banner =
      '<span><strong>Fictional demo.</strong> These plays illustrate the dashboard and are not your listening history.</span><a href="./">Return to your data</a>';
  else if (statusData.sync.error)
    banner = `<span><strong>Sync paused.</strong> ${esc(statusData.sync.error)}</span>`;
  else if (imp.cursor && !imp.complete) {
    const pct =
      imp.upper === imp.lower
        ? 0
        : Math.min(
            99,
            Math.round(
              ((imp.upper - imp.cursor) / (imp.upper - imp.lower)) * 100,
            ),
          );
    banner = `<span><strong>Importing your history.</strong> ${number(statusData.counts.plays)} plays saved. ${pct}% of the date span checked. You can explore while it runs.<div class="progress-track"><span style="width:${pct}%"></span></div></span>`;
  }
  const playing = statusData.now_playing.track;
  $("#now-playing").hidden = !playing || state.source !== "all";
  $("#now-playing").innerHTML = playing
    ? `<span class="playing-dot"></span><span><strong>Now playing</strong> ${esc(playing.title)} · ${esc(playing.artist)}</span><small>Reported by Last.fm at ${formatDate(statusData.now_playing.checked_at, { hour: "2-digit", minute: "2-digit" })}</small>`
    : "";
  $("#banner").innerHTML = banner;
  $("#banner").hidden = !banner;
}
async function load() {
  const serial = ++loadSerial;
  requestController?.abort();
  requestController = new AbortController();
  const signal = requestController.signal;
  $("#error").hidden = true;
  $("#content").setAttribute("aria-busy", "true");
  $("#page-title").textContent = titles[state.view][0];
  $("#page-description").textContent = titles[state.view][1] + (state.source === "vinyl" ? " Showing confirmed vinyl scrobbles." : state.source === "unknown" ? " Showing scrobbles without confirmed attribution." : "");
  document.querySelectorAll("[data-view]").forEach((b) => {
    if (b.closest("nav") || b.classList.contains("settings-nav"))
      b.setAttribute(
        "aria-current",
        b.dataset.view === state.view ? "page" : "false",
      );
  });
  document
    .querySelectorAll("[data-mode]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", b.dataset.mode === state.mode),
    );
  $("#toolbar").hidden = state.view === "settings";
  $("#period").value = state.period;
  $("#source").value = state.source;
  $("#now-playing").hidden = state.source !== "all" || !statusData?.now_playing?.track;
  $("#custom-dates").hidden = state.period !== "custom";
  $("#start").value = state.start;
  $("#end").value = state.end;
  urlState();
  try {
    if (!statusData) await fetchStatus();
    if (serial !== loadSerial) return;
    if (!statusData.configured && !statusData.demo) {
      $("#content").innerHTML = setupHTML();
      return;
    }
    let html;
    if (["overview", "trends"].includes(state.view)) {
      const data = await api("overview", {}, { signal });
      html = state.view === "overview" ? overviewHTML(data) : trendsHTML(data);
    } else if (state.view === "history") {
      html = historyHTML(
        await api("history", { q: state.q, offset: state.offset }, { signal }),
      );
    } else if (state.view === "settings") {
      await fetchStatus();
      html = settingsHTML(
        await api(
          "rankings",
          {
            kind: state.groupKind,
            period: "all",
            mode: "merged",
            entity: null,
            id: null,
            q: state.q,
            offset: state.offset,
          },
          { signal },
        ),
      );
    } else {
      const kind = { artists: "artist", albums: "album", songs: "song" }[
        state.view
      ];
      html = rankTable(
        await api(
          "rankings",
          { kind, q: state.q, offset: state.offset },
          { signal },
        ),
        kind,
      );
    }
    if (serial === loadSerial) $("#content").innerHTML = html;
  } catch (error) {
    report(error);
  } finally {
    if (serial === loadSerial) $("#content").setAttribute("aria-busy", "false");
  }
}
async function showDetail(kind, id, groupMode = false) {
  const serial = ++detailSerial;
  const dialog = $("#detail-dialog");
  const mode = groupMode ? "merged" : state.mode;
  const extra = {
    entity: kind,
    id,
    mode,
    period: groupMode ? "all" : state.period,
  };
  $("#detail-kind").textContent = kind.toUpperCase();
  $("#detail-content").innerHTML =
    '<div class="loading">Loading details…</div>';
  if (!dialog.open) dialog.showModal();
  try {
    const [detail, data] = await Promise.all([
      api("detail", extra),
      api("overview", extra),
    ]);
    if (serial !== detailSerial || !dialog.open) return;
    $("#detail-content").innerHTML =
      `<h2>${esc(detail.name)}</h2><p>${esc(detail.artist)}</p>${metrics(data, mode)}<section class="panel">${panelHead("Listening history", "Select a bar to inspect individual scrobbles.")}${chart(data, { kind, id, name: detail.name }, mode)}</section>${detail.versions.length ? `<div class="panel-head" style="margin-top:23px"><div><h2>Versions</h2><p>All-time plays for the selected source, including versions outside the selected period.</p></div></div><div class="table-wrap"><table><thead><tr><th>Scrobbled name</th><th class="num">Plays</th><th>First / latest play</th><th></th></tr></thead><tbody>${detail.versions.map((v) => `<tr><td class="name-cell"><strong>${esc(v.name)}</strong><small>${v.manual ? "Manual decision" : "Automatic grouping"}</small></td><td class="num">${number(v.plays)}</td><td><small>${formatDate(v.first_play)}<br>${formatDate(v.last_play)}</small></td><td>${detail.versions.length > 1 ? `<button class="button" data-separate="${v.id}" data-name="${esc(v.name)}">Separate</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : ""}<div class="dialog-actions" style="margin-top:18px"><button class="button primary" data-show-history="${kind}" data-id="${esc(id)}" data-name="${esc(detail.name)}" data-mode="${mode}" data-all="${groupMode}">View scrobbles →</button></div>`;
  } catch (error) {
    $("#detail-content").innerHTML =
      `<div class="empty">${esc(error.message)}</div>`;
  }
}
function confirmAction(title, description, fn) {
  $("#confirm-title").textContent = title;
  $("#confirm-description").textContent = description;
  const dialog = $("#confirm-dialog");
  $("#confirm-ok").onclick = async () => {
    dialog.close();
    try {
      await fn();
    } catch (error) {
      report(error);
    }
  };
  dialog.showModal();
}
async function groupAction(action, ids = []) {
  await api(
    "grouping",
    {},
    { method: "POST", body: JSON.stringify({ action, ids }) },
  );
  state.selected.clear();
  $("#detail-dialog").close();
  await fetchStatus();
  await load();
  toast(
    action === "undo"
      ? "Latest grouping change undone."
      : "Grouping updated. Your original scrobbles are preserved.",
  );
}
document.addEventListener("click", (event) => {
  const b = event.target.closest("button");
  if (!b || b.disabled) return;
  if (b.dataset.view) {
    changeView(b.dataset.view);
    return;
  }
  if (b.dataset.showHistory) {
    state.view = "history";
    state.filter = {
      kind: b.dataset.showHistory,
      id: b.dataset.id,
      name: b.dataset.name,
    };
    state.mode = b.dataset.mode;
    state.q = "";
    state.offset = 0;
    if (b.dataset.all === "true") state.period = "all";
    $("#detail-dialog").close();
    load();
    return;
  }
  if (b.dataset.mode) {
    state.mode = b.dataset.mode;
    state.filter = null;
    state.offset = 0;
    load();
    return;
  }
  if (b.dataset.detail) {
    showDetail(
      b.dataset.detail,
      b.dataset.id,
      b.dataset.groupDetail === "true",
    );
    return;
  }
  if (b.dataset.chartStart) {
    state.view = "history";
    state.period = "custom";
    state.start = b.dataset.chartStart;
    state.end = b.dataset.chartEnd;
    state.q = "";
    state.offset = 0;
    if (b.dataset.historyMode) state.mode = b.dataset.historyMode;
    if (b.dataset.entity)
      state.filter = {
        kind: b.dataset.entity,
        id: b.dataset.id,
        name: b.dataset.name,
      };
    $("#detail-dialog").close();
    load();
    return;
  }
  if (b.dataset.page) {
    state.offset = Math.max(
      0,
      state.offset + (b.dataset.page === "next" ? 50 : -50),
    );
    load();
    return;
  }
  if (b.dataset.separate) {
    confirmAction(
      "Separate this version?",
      `“${b.dataset.name}” will get its own total. Future imports will respect this choice. You can undo the change in Settings.`,
      () => groupAction("separate", [Number(b.dataset.separate)]),
    );
    return;
  }
  if (b.id === "clear-filter") {
    state.filter = null;
    state.offset = 0;
    load();
  }
  if (b.id === "undo") groupAction("undo").catch(report);
  if (b.id === "merge") {
    const entries = [...state.selected.entries()];
    confirmAction(
      "Combine selected groups?",
      `${entries.map((e) => "“" + e[1] + "”").join(", ")} will be counted together under “${entries[0][1]}”. Future matching versions will join that group. Original scrobbles stay intact.`,
      () =>
        groupAction(
          "merge",
          entries.map((e) => Number(e[0])),
        ),
    );
  }
});
document.addEventListener("change", (event) => {
  if (event.target.classList.contains("group-select")) {
    const el = event.target;
    if (el.checked) state.selected.set(el.dataset.id, el.dataset.name);
    else state.selected.delete(el.dataset.id);
    $("#selected-count").textContent = `${state.selected.size} selected`;
    $("#merge").disabled = state.selected.size < 2;
  }
  if (event.target.id === "group-kind") {
    state.groupKind = event.target.value;
    state.offset = 0;
    state.q = "";
    state.selected.clear();
    load();
  }
});
document.addEventListener("input", (event) => {
  if (event.target.id !== "search") return;
  clearTimeout(searchTimer);
  const value = event.target.value;
  searchTimer = setTimeout(async () => {
    state.q = value;
    state.offset = 0;
    await load();
    const search = $("#search");
    if (search) {
      search.focus();
      search.setSelectionRange?.(value.length, value.length);
    }
  }, 300);
});
$("#source").onchange = () => {
  state.source = $("#source").value;
  state.offset = 0;
  load();
};
$("#period").onchange = () => {
  state.period = $("#period").value;
  state.offset = 0;
  if (state.period === "custom") {
    $("#custom-dates").hidden = false;
    if (!state.start) {
      const today = new Date().toISOString().slice(0, 10);
      state.start = today;
      state.end = today;
      $("#start").value = today;
      $("#end").value = today;
    }
    return;
  }
  load();
};
$("#apply-dates").onclick = () => {
  state.start = $("#start").value;
  state.end = $("#end").value;
  state.offset = 0;
  load();
};
$("#close-detail").onclick = () => {
  $("#detail-dialog").close();
  detailSerial++;
};
$("#confirm-cancel").onclick = () => $("#confirm-dialog").close();
$("#theme").onclick = () => {
  const next =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("listening-theme", next);
  } catch (_) {}
};
$("#refresh").onclick = async () => {
  try {
    const response = await api("sync", {}, { method: "POST", body: "{}" });
    toast(response.message);
  } catch (e) {
    report(e);
  }
};
setInterval(async () => {
  if (document.hidden) return;
  try {
    const old = statusData;
    await fetchStatus();
    if (
      old &&
      (old.last_sync !== statusData.last_sync ||
        old.counts.plays !== statusData.counts.plays) &&
      !$("#detail-dialog").open &&
      document.activeElement?.id !== "search"
    )
      load();
  } catch (_) {}
}, 15000);
load();
