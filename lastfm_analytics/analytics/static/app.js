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
let savedMode = "merged";
try { savedMode = localStorage.getItem("listening-version-mode") || "merged"; } catch (_) {}
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
  period: initial.get("period") || "all",
  source: ["vinyl", "unknown"].includes(initial.get("source")) ? initial.get("source") : "all",
  mode: (initial.get("mode") || savedMode) === "raw" ? "raw" : "merged",
  start: initial.get("start") || "",
  end: initial.get("end") || "",
  demo: initial.get("demo") === "1",
  q: initial.get("q") || "",
  offset: Math.max(0, Number(initial.get("offset")) || 0),
  filter: initial.get("entity") && initial.get("id") ? {kind:initial.get("entity"),id:initial.get("id"),name:initial.get("name") || initial.get("entity")} : null,
  albumSort: initial.get("album_sort") === "estimated" ? "estimated" : "scrobbles",
  groupKind: "song",
  groupTab: "suggested",
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
  if (response.status === 401) {
    location.assign(new URL("login", document.baseURI));
    throw new Error("Sign in to continue.");
  }
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
function navigationState() {
  const keys = ["view", "period", "source", "mode", "start", "end", "demo", "q", "offset", "filter", "albumSort", "groupKind", "groupTab"];
  return Object.fromEntries(keys.map(key => [key, state[key]]));
}
function urlState(historyMode = "push") {
  if (historyMode === "none") return;
  const nav = navigationState();
  const q = query({ view: state.view, q:state.q, offset:state.offset || null, album_sort:state.albumSort === "estimated" ? "estimated" : null, name:state.filter?.name });
  const url = `${location.pathname}?${q}`;
  const current = history.state;
  if (!current?.listening || historyMode === "replace") {
    history.replaceState({listening:true, nav, detail:null}, "", url);
  } else if (JSON.stringify(current.nav) !== JSON.stringify(nav)) {
    history.pushState({listening:true, nav, detail:null}, "", url);
  } else if (current.detail && !$("#detail-dialog").open) {
    history.replaceState({listening:true, nav, detail:null}, "", url);
  }
}
window.addEventListener("popstate", async event => {
  if (!event.state?.listening) return;
  clearTimeout(searchTimer);
  detailSerial++;
  $("#confirm-dialog").close();
  $("#detail-dialog").close();
  Object.assign(state, event.state.nav);
  state.selected.clear();
  const serial = loadSerial + 1;
  await load({historyMode:"none"});
  if (serial === loadSerial && event.state.detail) {
    const {kind,id,groupMode} = event.state.detail;
    showDetail(kind, id, groupMode, true);
  }
});
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
function metrics(data, mode = state.mode, filter = state.filter, all = false) {
  return `<div class="metrics">${[
    ["plays", "Scrobbles", "↗", "history"],
    ["artists", "Artists", "◎", "artists"],
    ["albums", "Albums", "▣", "albums"],
    ["songs", mode === "raw" ? "Song entries" : "Songs", "♫", "songs"],
  ].map(([k, label, icon, view]) =>
    `<button type="button" class="metric metric-link" data-browse="${view}" data-mode="${mode}" data-all="${all}" ${filter ? `data-entity="${esc(filter.kind)}" data-id="${esc(filter.id)}" data-name="${esc(filter.name)}"` : ""} aria-label="View ${label.toLowerCase()}${filter ? ` for ${esc(filter.name)}` : ""}"><span class="metric-label">${label}<span aria-hidden="true">${icon}</span></span><span class="metric-value">${number(data.current[k])}</span><span class="metric-comparison">${difference(data.current[k], data.previous?.[k], data.period.compare)}</span></button>`
  ).join("")}</div>`;
}
function filterChip() {
  return state.filter ? `<div class="filter-chip">${esc(state.filter.name)}<button class="icon-button" id="clear-filter" aria-label="Clear item filter">×</button></div>` : "";
}
function panelHead(title, sub, action = "") {
  return `<div class="panel-head"><div><h2>${title}</h2>${sub ? `<p>${sub}</p>` : ""}</div>${action}</div>`;
}
function calendarHeatmap(data, filter = null, mode = state.mode) {
  const months = new Map();
  for (const b of data.timeline) {
    const key = b.label.slice(0, 7);
    const prior = months.get(key);
    if (prior) { prior.plays += b.plays; prior.end = b.end; }
    else months.set(key, { ...b, label: key });
  }
  const years = [...new Set([...months.keys()].map(k => k.slice(0, 4)))];
  const max = Math.max(1, ...[...months.values()].map(b => b.plays));
  const ds = filter ? ` data-entity="${esc(filter.kind)}" data-id="${esc(filter.id)}" data-name="${esc(filter.name)}" data-history-mode="${mode}"` : "";
  return `<section class="calendar-section"><h3>Listening by year and month</h3><small>Selected period. Darker cells mean more scrobbles. Tap a month to explore.</small><div class="calendar-scroll"><div class="calendar-heatmap"><span></span>${["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"].map(m=>`<span class="calendar-month">${m}</span>`).join("")}${years.map(y=>`<span class="calendar-year">${y}</span>${Array.from({length:12},(_,i)=> {
    const b = months.get(`${y}-${String(i+1).padStart(2,"0")}`);
    return b ? `<button class="calendar-cell" data-chart-start="${b.start}" data-chart-end="${b.end}"${ds} style="--intensity:${b.plays ? .18 + .82 * b.plays / max : 0}" title="${b.label}: ${number(b.plays)} scrobbles" aria-label="${b.label}, ${number(b.plays)} scrobbles"></button>` : '<span class="calendar-cell unavailable" title="Outside selected period"></span>';
  }).join("")}`).join("")}</div></div></section>`;
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
  return `<div class="chart-tools"><div class="chart-zoom-buttons" role="group" aria-label="Timeline zoom"><button class="button" data-chart-zoom="out" aria-label="Zoom out">−</button><button class="button" data-chart-zoom="reset" aria-label="Reset timeline zoom">Reset</button><button class="button" data-chart-zoom="in" aria-label="Zoom in">+</button></div><small>Pinch to zoom. Scroll sideways. Select a bar for scrobbles.</small></div><div class="chart-layout"><div class="chart-axis"><span>${number(rounded)}</span><span>${number(rounded * 0.75)}</span><span>${number(rounded * 0.5)}</span><span>${number(rounded * 0.25)}</span><span>0</span></div><div class="chart-scroll" tabindex="0" aria-label="Scrollable listening timeline"><div class="bar-chart" style="--bins:${bins.length}" role="group" aria-label="Scrobbles by ${bins[0]?.label.length === 7 ? "month" : "day"}, select a bar to inspect history">${bins.map((b,i) => `<button data-label="${i % 6 === 0 ? esc(b.label) : ""}" data-chart-start="${b.start}" data-chart-end="${b.end}"${ds} style="height:${Math.max(0.5, (b.plays / rounded) * 100)}%" title="${b.label}: ${number(b.plays)} scrobbles. Open history." aria-label="${b.label}, ${number(b.plays)} scrobbles"></button>`).join("")}</div></div></div><div class="chart-foot"><span><strong>${number(data.current.plays)}</strong> scrobbles in this period</span><span>${best.plays ? `Peak: <strong>${esc(best.label)}</strong>` : "No plays yet"}</span></div>${calendarHeatmap(data, filter, mode)}`;
}
function detailAttrs(kind, row) {
  return `data-detail="${kind}" data-id="${esc(row.id)}"`;
}
function topList(title, kind, rows, view, metric = "scrobbles", scope = null) {
  return `<section class="panel">${panelHead(title, "", `<button class="button quiet" data-view="${view}" ${scope ? `data-scope-kind="${esc(scope.kind)}" data-scope-id="${esc(scope.id)}" data-scope-name="${esc(scope.name)}"` : ""}>View all ↗</button>`)}<div>${
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
              )}</span><span class="list-name"><strong>${esc(r.name)}</strong><small>${esc(r.artist || "Artist")}${r.versions > 1 ? ` · ${r.versions} versions` : ""}</small></span><span class="list-count" title="${metric === "estimated" ? "Estimated album listens" : "Track scrobbles"}">${metric === "estimated" ? (r.estimated_listens === null ? "—" : number(r.estimated_listens)) : number(r.plays)}</span></button>`,
          )
          .join("")
      : `<div class="empty">${metric === "estimated" ? "No album estimates yet. Tracklists are being identified in the background." : "No listening in this period."}</div>`
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
  return `${metrics(data)}<div class="grid-two"><section class="panel">${panelHead("Listening activity", `${data.period.start_label} to ${data.period.end_label}`, '<span class="pill">Scrobbles</span>')}${chart(data)}</section>${discovery(data)}</div>${insightsStrip(data)}<div class="grid-equal">${topList("Top artists", "artist", data.top_artists, "artists")}${topList("Top albums by scrobbles", "album", data.top_albums, "albums")}${topList("Top songs", "song", data.top_songs, "songs")}</div><div class="grid-two" style="margin-top:18px">${topList("Top albums by estimated listens", "album", data.top_albums_estimated || [], "albums", "estimated")}<section class="panel"><h2>Estimated album listens</h2><p class="method-note">The third least played track from an identified complete tracklist, including tracks with zero plays. Albums must contain at least six tracks. This avoids favouring longer albums. Figures appear as tracklists are identified in the background.</p><button class="button quiet" data-view="albums">Browse album rankings →</button></section></div>`;
}
function trendsHTML(data) {
  return `${metrics(data)}<section class="panel" style="margin-bottom:22px">${panelHead("Your listening over time", "Select any bar to explore the plays behind it.")}${chart(data)}</section><div class="grid-two">${heatmap(data)}<section class="panel">${panelHead("Artist concentration", "Share of plays from your five most played artists")}<div class="insight-number">${data.top_five_share}%</div><p class="method-note">${number(data.current.artists)} artists in this period. A higher share means more listening centred on your favourites.</p><div class="split-bar"><span style="width:${data.top_five_share}%"></span></div><div class="legend"><span>Top five</span><span>Other artists</span></div></section></div><div class="grid-two">${discovery(data)}<section class="panel">${panelHead("Rediscoveries", "At least 90 days since the previous recorded play")}${data.returning.map((r) => `<button class="list-row" ${detailAttrs("artist", r)}><span class="list-name"><strong>${esc(r.name)}</strong><small>${number(Math.floor(r.gap_days))} days between plays</small></span><span class="list-count">${number(r.plays)}</span></button>`).join("") || '<p class="method-note">No rediscoveries in this period.</p>'}</section></div>`;
}
function pager(total) {
  return `<div class="pager"><span>${total ? `${number(state.offset + 1)}–${number(Math.min(state.offset + 50, total))} of ${number(total)}` : "No results"}</span><div><button class="button" data-page="prev" ${state.offset === 0 ? "disabled" : ""}>Previous</button><button class="button" data-page="next" ${state.offset + 50 >= total ? "disabled" : ""}>Next</button></div></div>`;
}
function rankTable(data, kind, settings = false) {
  const max = data.rows[0]?.plays || 1;
  const albumControls = kind === "album" && !settings
    ? `<div class="segment" role="group" aria-label="Album ranking measure"><button data-album-sort="scrobbles" aria-pressed="${state.albumSort === "scrobbles"}">Rank by scrobbles</button><button data-album-sort="estimated" aria-pressed="${state.albumSort === "estimated"}">Rank by estimated listens</button></div><p class="method-note">Estimated album listens use the third least played track of a complete tracklist with at least six songs. Unknown tracklists show a dash until identified.</p>`
    : "";
  return `${settings ? "" : filterChip()}${albumControls}<section class="panel table-panel"><div class="table-head"><div><h2>${settings ? "Canonical groups" : `Ranked ${state.view}`}</h2><small>${settings ? "All time. Select groups by the same artist to combine." : `${number(data.total)} results · ${state.mode === "raw" ? "Original Last.fm names" : "Combined versions"}`}</small></div><input id="search" aria-label="Search ${settings ? "groups" : state.view}" type="search" placeholder="Search ${settings ? "groups" : state.view}…" value="${esc(state.q)}"></div>${settings ? `<div class="selection-bar"><select id="group-kind" aria-label="Grouping type"><option value="song" ${kind === "song" ? "selected" : ""}>Songs</option><option value="album" ${kind === "album" ? "selected" : ""}>Albums</option></select><span id="selected-count">${state.selected.size} selected</span><button class="button primary" id="merge" ${state.selected.size < 2 ? "disabled" : ""}>Merge selected</button></div>` : ""}<div class="table-wrap"><table><thead><tr><th>${settings ? "Select" : "#"}</th><th>${kind === "artist" ? "Artist" : kind === "song" ? "Song / artist" : "Album / artist"}</th><th class="num">Scrobbles</th>${kind === "album" && !settings ? '<th class="num">Est. listens</th>' : ""}<th class="num">${settings ? "Versions" : "Previous"}</th><th class="num">${settings ? "" : "Change"}</th></tr></thead><tbody>${data.rows.map((r, i) => `<tr><td>${settings ? `<input type="checkbox" class="group-select" data-id="${r.id}" data-name="${esc(r.name)}" ${state.selected.has(String(r.id)) ? "checked" : ""} aria-label="Select ${esc(r.name)} by ${esc(r.artist)}">` : state.offset + i + 1}</td><td class="name-cell"><button class="text-button" ${detailAttrs(kind, r)} ${settings ? 'data-group-detail="true"' : ""}><strong>${esc(r.name)}</strong></button><small>${esc(r.artist)}${kind !== "artist" && r.versions > 1 ? ` · ${number(r.versions)} versions` : ""}</small></td><td class="num"><strong>${number(r.plays)}</strong><div class="row-progress"><i style="width:${(r.plays / max) * 100}%"></i></div></td>${kind === "album" && !settings ? `<td class="num"><strong title="${r.estimate_status === "ready" ? `${r.track_count} tracks · ${r.tracklist_source}` : "Complete tracklist not yet identified"}">${r.estimated_listens === null ? "—" : number(r.estimated_listens)}</strong></td>` : ""}<td class="num">${settings ? number(r.versions) : r.previous === null ? "—" : number(r.previous)}</td><td class="num">${settings ? "" : r.previous === null ? "—" : !r.previous ? '<span class="change">New</span>' : `<span class="change">${r.plays >= r.previous ? "+" : ""}${(((r.plays - r.previous) / r.previous) * 100).toFixed(1)}%</span>`}</td></tr>`).join("") || '<tr><td colspan="${kind === "album" && !settings ? 6 : 5}" class="empty">No results for this search or date range.</td></tr>'}</tbody></table></div>${pager(data.total)}</section>`;
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
  return `${filterChip()}<section class="panel table-panel"><div class="table-head"><div><h2>Your listening diary</h2><small>${number(data.total)} scrobbles · ${esc(statusData.timezone)}</small></div><input id="search" type="search" aria-label="Search history" placeholder="Search songs, artists or albums…" value="${esc(state.q)}"></div><div class="table-wrap"><table class="history-table"><colgroup><col class="history-time"><col class="history-song"><col class="history-artist"><col class="history-album"></colgroup><thead><tr><th>Time</th><th>Song</th><th>Artist</th><th>Album</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="empty">No scrobbles match this selection.</td></tr>'}</tbody></table></div>${pager(data.total)}</section>`;
}
function updateCandidateName(card) {
  const choices = [...card.querySelectorAll(".candidate-version:checked")]
    .sort((a, b) => Number(/[()[\]{}]/.test(a.dataset.name)) - Number(/[()[\]{}]/.test(b.dataset.name))
      || Number(b.dataset.plays) - Number(a.dataset.plays)
      || a.dataset.name.localeCompare(b.dataset.name));
  const input = card.querySelector(".candidate-name");
  const select = card.querySelector(".candidate-name-choice");
  input.disabled = select.disabled = choices.length < 2;
  select.innerHTML = choices.map(v => `<option value="${v.value}" data-name="${esc(v.dataset.name)}">${esc(v.dataset.name)} (${number(v.dataset.plays)} scrobbles)</option>`).join("")
    + '<option value="custom">Custom name…</option>';
  if (input.dataset.edited) {
    select.value = "custom";
    return;
  }
  const preferred = choices.find(v => v.value === select.dataset.chosen) || choices[0];
  if (preferred) {
    select.value = preferred.value;
    input.value = preferred.dataset.name;
  } else {
    input.value = "";
  }
}

function settingsHTML(data) {
  if (state.groupKind === "artist") {
    const suggested = (data.suggestions || []).map(item =>
      '<article class="review-card"><strong>' + esc(item.names.join(' ↔ ')) +
      '</strong><p class="method-note">' + esc(item.reason) + ' · ' + number(item.plays) + ' scrobbles</p>' +
      '<button class="button primary" data-suggest-artist-a="' + esc(item.ids[0]) +
      '" data-suggest-artist-b="' + esc(item.ids[1]) +
      '" data-suggest-name="' + esc(item.names[0]) + '">Review merge</button></article>').join('');
    const entries = data.rows.map(a => '<article class="review-card"><label><input class="group-select" type="checkbox" data-id="' +
      esc(a.id) + '" data-name="' + esc(a.name) + '" ' +
      (state.selected.has(String(a.id)) ? 'checked' : '') + '><strong>' + esc(a.name) +
      '</strong> · ' + number(a.plays) + ' plays</label>' +
      (a.versions > 1 ? '<p class="method-note">Combined names: ' + esc(a.originals.join(' · ')) + '</p>' : '') +
      '</article>').join('');
    return '<section class="panel"><div class="panel-head"><div><h2>Artist identities</h2><p>Combine alternative names for the same artist. Identically named songs and albums combine automatically while explicit manual separations stay intact.</p></div>' +
      '<button class="button" id="undo" ' + (statusData.events.some(e => !e.undone) ? '' : 'disabled') + '>Undo latest change</button></div>' +
      '<div class="review-filters"><select id="group-kind" aria-label="Review artist, song or album merges"><option value="artist" selected>Artists</option><option value="song">Songs</option><option value="album">Albums</option></select>' +
      '<input id="search" type="search" placeholder="Search artist names…" aria-label="Search artists" value="' + esc(state.q) + '"></div>' +
      '<div class="selection-bar"><span id="selected-count">' + state.selected.size + ' selected</span>' +
      '<label>Combined artist name <input id="artist-name" type="text" maxlength="1000" placeholder="Use the selected artist name"></label>' +
      '<button class="button primary" id="merge-artists" ' + (state.selected.size < 2 ? 'disabled' : '') + '>Merge selected artists</button></div>' +
      (suggested ? '<h3 class="settings-version-heading">Suggested artist matches</h3><p class="method-note">Suggestions only. Nothing combines until you approve the pair.</p><div class="review-list">' + suggested + '</div>' : '') +
      '<h3 class="settings-version-heading">All artists</h3><div class="review-list">' + (entries || '<div class="empty">No artists found.</div>') + '</div>' +
      pager(data.total) + '</section>';
  }
  const card = r => state.groupTab === "merged"
    ? `<article class="review-card"><h3>${esc(r.artist)}</h3><button class="text-button" data-detail="${state.groupKind}" data-id="${r.id}" data-group-detail="true">${esc(r.name)} · ${r.versions.length} versions · ${number(r.plays)} plays</button><p>${r.versions.map(v => esc(v.name)).join(" · ")}</p></article>`
    : `<article class="review-card"><h3>${esc(r.artist)}</h3><fieldset class="review-choices"><legend>Choose the versions to combine</legend>${r.versions.map(v => `<label><input type="checkbox" class="candidate-version" value="${v.id}" data-name="${esc(v.name)}" data-plays="${v.plays}"><span>${esc(v.name)}<small>${number(v.plays)} scrobbles</small></span></label>`).join("")}</fieldset><label class="merge-name">Name from selected versions<select class="candidate-name-choice" disabled><option value="">Select at least two versions</option></select></label><label class="merge-name">Combined name, editable<input class="candidate-name" type="text" maxlength="1000" placeholder="Select versions, then choose a name" disabled></label><p>${esc(r.reason)}</p><div class="review-actions"><button class="button primary" data-candidate-merge="${r.ids.join(",")}" disabled>Merge selected</button>${r.learnable ? `<button class="button" data-candidate-learn="${r.ids.join(",")}" data-suffix="${esc(r.suffix)}" data-artist="${esc(r.artist)}" title="Select every listed version to learn this suffix" disabled>Merge and learn suffix</button>` : ""}${state.groupTab !== "skipped" || r.dismissed ? `<button class="button" data-candidate-key="${esc(r.key)}" data-candidate-action="${state.groupTab === "skipped" ? "restore" : "dismiss"}">${state.groupTab === "skipped" ? "Reconsider" : "Keep separate"}</button>` : ""}</div></article>`;
  return `<section class="panel"><div class="panel-head"><div><h2>Version review</h2><p>Select two or more versions to combine. Unselected versions keep their current grouping.</p></div><button class="button" id="undo" ${statusData.events.some(e => !e.undone) ? "" : "disabled"}>Undo latest change</button></div><div class="review-filters"><select id="group-kind" aria-label="Review songs or albums"><option value="artist">Artists</option><option value="song" ${state.groupKind === "song" ? "selected" : ""}>Songs</option><option value="album" ${state.groupKind === "album" ? "selected" : ""}>Albums</option></select><input id="search" type="search" aria-label="Search merge candidates" placeholder="Search candidates or merges…" value="${esc(state.q)}"></div><div class="segment review-tabs">${[["suggested","Suggestions"],["skipped","Skipped candidates"],["merged","Already merged"]].map(([tab,label])=>`<button data-group-tab="${tab}" aria-pressed="${tab===state.groupTab}">${label}</button>`).join("")}</div><p class="method-note">Skipped candidates include live performances and mixes that automatic rules kept separate, plus your rejected suggestions. “Merge and learn suffix” remembers only that exact suffix for this artist and type, for future imports with a matching base entry. Undo removes the rule and its later assignments.</p><div class="review-list">${data.rows.map(card).join("") || '<div class="empty">No matches in this review.</div>'}</div>${pager(data.total)}</section><section class="panel" style="margin-top:20px"><h2>Learned rules</h2>${data.rules.length ? data.rules.map(r=>`<p class="method-note">${esc(r.artist)} · ${esc(r.kind)} · ${esc(r.suffix)}</p>`).join("") : '<p class="method-note">Approve a learnable suggestion to create a rule.</p>'}<h3 style="margin-top:20px">Connection and data</h3><p class="method-note">${esc(statusData.username)} · ${number(statusData.counts.plays)} scrobbles · ${esc(statusData.timezone)} · ${statusData.import_state.complete ? "Import complete" : "Import in progress"}. Configure credentials and the web password in Home Assistant. Vinyl reporting: ${statusData.source_reporting_enabled ? "enabled" : "not configured"}, ${number(statusData.source_reports)} reports received.</p></section>`;
}
function setupHTML() {
  return `<section class="panel setup"><div class="setup-mark" aria-hidden="true">◫</div><h2>Your listening, ready to explore</h2><p>Connect Last.fm to bring your listening history together. Explore trends, find returning favourites and combine versions into meaningful totals.</p><ol><li>Open this add-on’s <strong>Configuration</strong> tab in Home Assistant.</li><li>Enter your <strong>Last.fm username</strong> and <a href="https://www.last.fm/api/account/create" target="_blank" rel="noopener noreferrer">API key</a>.</li><li>Save, restart the add-on and reopen this dashboard.</li></ol><p>The first import runs in the background and resumes after restarts. Your Last.fm history is never edited.</p><a class="button primary" href="?demo=1">Explore a fictional demo ↗</a></section>`;
}
async function fetchStatus() {
  statusData = await api("status");
  const yearGroup = $("#year-options");
  const years = statusData.years || [];
  yearGroup.innerHTML = years.map(y => `<option value="year:${y}">${y}</option>`).join("");
  $("#period").value = state.period;
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
const cacheTimers = {};
function cacheLabel(data, target) {
  const marker = $(target);
  const cache = data?._cache;
  marker.hidden = !cache;
  if (!cache) return;
  const updated = formatDate(cache.generated, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  marker.textContent = `Saved view · updated ${updated}${cache.error ? " · Update delayed; showing saved results" : cache.stale || cache.refreshing ? " · Updating…" : ""}`;
}
function replaceCachedView(container, html) {
  const element = $(container);
  const positions = [...element.querySelectorAll(".chart-scroll")].map(el => ({ left: el.scrollLeft, zoom: Number(el.dataset.zoom || 1) }));
  const top = element.scrollTop;
  element.innerHTML = html;
  element.scrollTop = top;
  element.querySelectorAll(".chart-scroll").forEach((el, i) => {
    if (!positions[i]) return;
    setChartZoom(el, positions[i].zoom);
    el.scrollLeft = positions[i].left;
  });
}
function watchCachedView(slot, path, extra, data, render, valid, target) {
  clearTimeout(cacheTimers[slot]);
  cacheLabel(data, target);
  const content = value => JSON.stringify({ ...value, _cache: undefined });
  let displayed = content(data);
  async function tick() {
    if (!valid()) return;
    if (document.hidden) { cacheTimers[slot] = setTimeout(tick, 15000); return; }
    try {
      const next = await api(path, extra);
      if (!valid()) return;
      const updated = content(next);
      if (updated !== displayed && document.activeElement?.id !== "search") {
        render(next);
        displayed = updated;
      }
      cacheLabel(next, target);
      data = next;
    } catch (_) {
      if (!valid()) return;
      $(target).textContent = "Update delayed; showing saved results";
    }
    cacheTimers[slot] = setTimeout(tick, data?._cache?.error ? 60000 : data?._cache?.stale || data?._cache?.refreshing ? 2000 : 60000);
  }
  if (data?._cache) cacheTimers[slot] = setTimeout(tick, data._cache.error ? 60000 : data._cache.stale || data._cache.refreshing ? 2000 : 60000);
}
async function load({historyMode = "push"} = {}) {
  const serial = ++loadSerial;
  clearTimeout(cacheTimers.main);
  $("#cache-status").hidden = true;
  requestController?.abort();
  requestController = new AbortController();
  const signal = requestController.signal;
  $("#error").hidden = true;
  $("#content").setAttribute("aria-busy", "true");
  $("#loading-status").hidden = false;
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
  $("#settings-preferences").hidden = state.view !== "settings";
  $("#period").value = state.period;
  $("#source").setAttribute("aria-pressed", state.source === "vinyl");
  $("#source").title = state.source === "vinyl" ? "Vinyl only is active. Show all listening" : "Show vinyl listening only";
  $("#now-playing").hidden = state.source !== "all" || !statusData?.now_playing?.track;
  $("#custom-dates").hidden = state.period !== "custom";
  $("#start").value = state.start;
  $("#end").value = state.end;
  urlState(historyMode);
  try {
    if (!statusData) await fetchStatus();
    if (serial !== loadSerial) return;
    if (!statusData.configured && !statusData.demo) {
      $("#content").innerHTML = setupHTML();
      return;
    }
    let html, cachedView;
    if (["overview", "trends"].includes(state.view)) {
      const data = await api("overview", {}, { signal });
      const view = state.view;
      const render = next => replaceCachedView("#content", view === "overview" ? overviewHTML(next) : trendsHTML(next));
      cachedView = { path: "overview", extra: {}, data, render };
      html = view === "overview" ? overviewHTML(data) : trendsHTML(data);
    } else if (state.view === "history") {
      html = historyHTML(
        await api("history", { q: state.q, offset: state.offset }, { signal }),
      );
    } else if (state.view === "settings") {
      await fetchStatus();
      html = settingsHTML(await api(state.groupKind === "artist" ? "artists-review" : "grouping-review",
        { kind: state.groupKind, tab: state.groupTab, q: state.q, offset: state.offset }, { signal }));
    } else {
      const kind = { artists: "artist", albums: "album", songs: "song" }[
        state.view
      ];
      const extra = { kind, q: state.q, offset: state.offset, album_sort:kind === "album" ? state.albumSort : null };
      const data = await api("rankings", extra, { signal });
      cachedView = { path: "rankings", extra, data, render: next => replaceCachedView("#content", rankTable(next, kind)) };
      html = rankTable(data, kind);
    }
    if (serial === loadSerial) {
      $("#content").innerHTML = html;
      if (cachedView) watchCachedView("main", cachedView.path, cachedView.extra, cachedView.data, cachedView.render, () => serial === loadSerial, "#cache-status");
    }
  } catch (error) {
    report(error);
  } finally {
    if (serial === loadSerial) {
      $("#content").setAttribute("aria-busy", "false");
      $("#loading-status").hidden = true;
    }
  }
}
function artistLogoHTML(detail) {
  // The logo takes the title's place only after its image has loaded.
  return `<h2 class="artist-title-fallback">${esc(detail.name)}</h2>${detail.artist_logo
    ? `<img class="artist-logo" src="${esc(detail.artist_logo)}" alt="${esc(detail.name)}" decoding="async" referrerpolicy="no-referrer" fetchpriority="high">`
    : ""}`;
}
function artworkHTML(detail, kind) {
  if (kind === "artist" && detail.artist_photo) {
    return `<figure class="detail-artwork artist-photo"><img src="${esc(detail.artist_photo)}" alt="${esc(detail.artist_name || detail.name)} artist photo" width="176" height="176" decoding="async" referrerpolicy="no-referrer"></figure>`;
  }
  if (!detail.artwork) {
    // A logo by itself is enough for an artist header, without a fallback box.
    if (kind === "artist" && detail.artist_logo) return "";
    return `<p class="method-note" role="status">${detail.artwork_pending ? "Loading artwork…" : "No album artwork available"}</p>`;
  }
  const art = detail.artwork;
  return `<figure class="detail-artwork"><img src="${esc(art.url)}" alt="Cover of ${esc(art.album)} by ${esc(art.artist)}" width="112" height="112" decoding="async" referrerpolicy="no-referrer"><figcaption>${kind === "album" ? "Album cover" : esc(art.album)} · ${esc(art.source || "Last.fm")}</figcaption></figure>`;
}
function spotifyLink(detail, kind) {
  const artist = kind === "artist" ? detail.name : detail.artist;
  const query = kind === "artist"
    ? `artist:"${artist}"`
    : kind === "album" ? `album:"${detail.name}" artist:"${artist}"`
    : `track:"${detail.name}" artist:"${artist}"`;
  const url = "https://open.spotify.com/search/" + encodeURIComponent(query);
  const icon = `<svg class="spotify-mark" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="11" fill="currentColor"/><path d="M5.1 8.8c4.4-1.35 9.9-.75 13.9 1.55M6.1 12c3.7-1.15 8.1-.5 11.45 1.5M7.1 15.05c3.05-.9 6.5-.4 9.25 1.25" stroke="#1ed760" stroke-width="1.7" stroke-linecap="round" fill="none"/></svg>`;
  return `<a class="button spotify-link" href="${url}" target="_blank" rel="noopener noreferrer" aria-label="Find ${esc(detail.name)} on Spotify">${icon}<span>Find on Spotify</span></a>`;
}
function detailHeaderHTML(detail, kind) {
  const links = `${spotifyLink(detail, kind)} ${recordStoreLink(detail, kind)} <span id="shelf-link-slot"></span>`;
  if (kind === "artist") {
    return `<div class="artist-detail-hero"><div id="artist-logo-slot" class="artist-logo-stage">${artistLogoHTML(detail)}</div><div class="detail-heading detail-media-actions artist-detail-heading"><div id="detail-artwork-slot">${artworkHTML(detail, kind)}</div><div class="artist-actions">${links}</div></div></div>`;
  }
  return `<div class="detail-entity-hero"><div class="detail-entity-title"><h2>${esc(detail.name)}</h2><p>${esc(detail.artist)}</p></div><div class="detail-heading detail-media-actions"><div id="detail-artwork-slot">${artworkHTML(detail, kind)}</div><div class="artist-actions">${links}</div></div></div>`;
}
function audioShelfLink(base, path, label) {
  // Same-window HTTPS navigation gives installed AudioShelf a chance to handle
  // its registered web-app scope instead of forcing an ordinary new browser tab.
  return `<a class="button" href="${esc(base + '/' + path)}">${esc(label)}</a>`;
}
function recordStoreLink(detail, kind) {
  const base = statusData.audioshelf_url;
  if (!base || statusData.demo) return '';
  const artist = kind === 'artist' ? detail.name : detail.artist;
  const album = kind === 'album' ? detail.name : detail.listening_albums?.[0];
  const searchKind = kind === 'artist' || !album ? 'artist' : 'album';
  const query = searchKind === 'artist' ? artist : album;
  return audioShelfLink(base, `#store/${searchKind}/${encodeURIComponent(query)}`, 'Find in record store');
}
async function loadShelfLinks(detail, kind, serial) {
  const base = statusData.audioshelf_url;
  if (!base || statusData.demo) return;
  const params = new URLSearchParams({kind, artist:kind === 'artist' ? detail.name : detail.artist, name:detail.name});
  (detail.listening_albums || []).forEach(album => params.append('album', album));
  try {
    const response = await fetch(base + '/api/listening-links?' + params, {credentials:'include', signal:AbortSignal.timeout(5000)});
    if (serial !== detailSerial || !$('#detail-dialog').open) return;
    const slot = $('#shelf-link-slot');
    if (!response.ok) {
      slot.innerHTML = '<small class="method-note">Sign in to AudioShelf to check your shelf.</small>';
      return;
    }
    const data = await response.json();
    slot.innerHTML = (data.matches || []).filter(m => /^#(?:shelf|album)\/[a-zA-Z0-9%-]+$/.test(m.path)).map(m => audioShelfLink(base, m.path,
      data.matches.length > 1 ? `On your shelf: ${m.label}` : 'On your shelf')).join(' ');
  } catch (_) {
    if (serial === detailSerial && $('#detail-dialog').open) $('#shelf-link-slot').innerHTML = '<small class="method-note">Shelf check unavailable.</small>';
  }
}
function bindArtworkError(detail, kind) {
  const logoStage = $("#artist-logo-slot");
  const logo = logoStage?.querySelector("img.artist-logo");
  if (logo) {
    const reveal = () => {
      if (logo.isConnected && logo.naturalWidth > 0) logoStage.classList.add("logo-ready");
    };
    logo.onload = reveal;
    // Cached images may finish before we install onload.
    if (logo.complete && logo.naturalWidth > 0) reveal();
  }
  document.querySelectorAll("#detail-hero .detail-artwork img, #detail-hero .artist-logo-stage img").forEach(img => {
    img.onerror = () => {
      const url = img.getAttribute("src");
      if (detail.artist_logo === url) {
        detail.artist_logo = null;
        const slot = $("#artist-logo-slot");
        if (slot) {
          slot.classList.remove("logo-ready");
          slot.innerHTML = artistLogoHTML(detail);
        }
        return;
      }
      if (detail.artist_photo === url) detail.artist_photo = null;
      if (detail.artwork?.url === url) detail.artwork = null;
      const figure = img.closest("figure");
      img.remove();
      if (figure && !figure.querySelector("img")) {
        $("#detail-artwork-slot").innerHTML = artworkHTML(detail, kind);
        bindArtworkError(detail, kind);
      }
    };
  });
}
async function showDetail(kind, id, groupMode = false, restoring = false) {
  if (!restoring) history.pushState({listening:true, nav:navigationState(), detail:{kind,id,groupMode}}, "", location.href);
  const serial = ++detailSerial;
  clearTimeout(cacheTimers.detail);
  $("#detail-cache-status").hidden = true;
  const dialog = $("#detail-dialog");
  dialog.classList.toggle("artist-detail-open", kind === "artist");
  const mode = groupMode ? "merged" : state.mode;
  const extra = {entity:kind,id,mode,period:groupMode?"all":state.period};
  $("#detail-kind").textContent = kind.toUpperCase();
  $("#detail-content").innerHTML = '<div class="loading">Loading details…</div>';
  if (!dialog.open) dialog.showModal();
  // Begin analytics immediately, but do not delay the visible header for it.
  const analyticsTask = api("overview", extra).then(data => ({data}), error => ({error}));
  try {
    const detail = await api("detail", extra);
    if (serial !== detailSerial || !dialog.open) return;
    $("#detail-content").innerHTML = `<div id="detail-hero">${detailHeaderHTML(detail, kind)}</div><div id="detail-analytics"><div class="loading">Loading listening analysis…</div></div>`;
    bindArtworkError(detail, kind);
    loadShelfLinks(detail, kind, serial);
    // Artwork is independent of the heavy analytics calculation.
    if (detail.artwork_pending) {
      (async () => {
        // Space out background artwork checks rather than polling every second.
        for (const delay of [1000, 1800, 3000, 5000, 8000, 11000]) {
          await new Promise(resolve => setTimeout(resolve, delay));
          if (serial !== detailSerial || !dialog.open) return;
          let next;
          try { next = await api("artwork", extra); } catch (_) { continue; }
          if (serial !== detailSerial || !dialog.open) return;
          const oldArtwork = artworkHTML(detail, kind), oldLogo = artistLogoHTML(detail);
          Object.assign(detail, next);
          if (artworkHTML(detail, kind) !== oldArtwork) {
            $("#detail-artwork-slot").innerHTML = artworkHTML(detail, kind);
            bindArtworkError(detail, kind);
          }
          const logoSlot = $("#artist-logo-slot");
          if (logoSlot && oldLogo !== artistLogoHTML(detail)) {
            logoSlot.classList.remove("logo-ready");
            logoSlot.innerHTML = artistLogoHTML(detail);
            bindArtworkError(detail, kind);
          }
          if (!next.artwork_pending) return;
        }
      })().catch(() => {});
    }
    const result = await analyticsTask;
    if (serial !== detailSerial || !dialog.open) return;
    if (result.error) throw result.error;
    const data = result.data;
    const renderDetail = next => {
      replaceCachedView("#detail-analytics",
        `${metrics(next, mode, {kind,id,name:detail.name}, groupMode)}<section class="panel">${panelHead("Listening history", "Select a bar to inspect individual scrobbles.")}${chart(next, {kind,id,name:detail.name}, mode)}</section>${kind === "album" ? albumEstimateHTML(next.album_estimate) : ""}${kind === "artist" ? artistAlbumsHTML(next, id, detail.name) : ""}${detail.versions.length ? `<div class="panel-head" style="margin-top:23px"><div><h2>Versions</h2><p>All-time plays for the selected source, including versions outside the selected period.</p></div></div><div class="table-wrap"><table><thead><tr><th>Scrobbled name</th><th class="num">Plays</th><th>First / latest play</th><th></th></tr></thead><tbody>${detail.versions.map(v => `<tr><td class="name-cell"><strong>${esc(v.name)}</strong><small>${v.manual ? "Manual decision" : "Automatic grouping"}</small></td><td class="num">${number(v.plays)}</td><td><small>${formatDate(v.first_play)}<br>${formatDate(v.last_play)}</small></td><td>${detail.versions.length > 1 ? `<button class="button" data-separate="${v.id}" data-name="${esc(v.name)}">Separate</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : ""}<div class="dialog-actions" style="margin-top:18px"><button class="button primary" data-show-history="${kind}" data-id="${esc(id)}" data-name="${esc(detail.name)}" data-mode="${mode}" data-all="${groupMode}">View scrobbles →</button></div>`);
    };
    renderDetail(data);
    watchCachedView("detail", "overview", extra, data, renderDetail, () => serial === detailSerial && dialog.open, "#detail-cache-status");
  } catch (error) {
    if (serial === detailSerial && dialog.open) {
      const target = $("#detail-analytics") || $("#detail-content");
      target.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    }
  }
}

function albumEstimateHTML(album) {
  if (!album) return "";
  const count = album.estimated_listens === null ? "—" : number(album.estimated_listens);
  const explanation = album.estimate_status === "ineligible"
    ? "Fewer than six tracks in the identified edition. No estimate is produced."
    : album.estimate_status !== "ready"
      ? "Identifying a complete tracklist in the background. No estimate is calculated from partial scrobbles."
      : `Third least played track of ${number(album.track_count)} canonical tracks · ${esc(album.tracklist_source || "tracklist metadata")}.`;
  const tracks = album.track_breakdown || [];
  return `<section class="panel" style="margin-top:18px"><div class="panel-head"><div><h2>Estimated album listens</h2><p>How often you listen through most of the album, rather than a count that favours longer records.</p></div><strong class="insight-number">${count}</strong></div><p class="method-note">${explanation}</p>${tracks.length ? `<div class="table-wrap"><table><thead><tr><th>Canonical track</th><th class="num">Scrobbles</th></tr></thead><tbody>${tracks.map(t => `<tr><td>${esc(t.title)}</td><td class="num">${number(t.plays)}</td></tr>`).join("")}</tbody></table></div>` : ""}</section>`;
}
function artistAlbumsHTML(data, id, name) {
  return `<div class="grid-two" style="margin-top:18px">${topList("Albums by scrobbles", "album", data.top_albums || [], "albums", "scrobbles", {kind:"artist", id, name})}${topList("Albums by estimated listens", "album", data.top_albums_estimated || [], "albums", "estimated", {kind:"artist", id, name})}</div>`;
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
async function groupAction(action, ids = [], key = null, name = undefined) {
  await api(
    "grouping",
    {},
    { method: "POST", body: JSON.stringify({ action, ids, key, name }) },
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
  if (b.dataset.albumSort) {
    state.albumSort = b.dataset.albumSort;
    state.offset = 0;
    load();
    return;
  }
  if (b.dataset.groupTab) {
    state.groupTab = b.dataset.groupTab;
    state.offset = 0; state.q = ""; load(); return;
  }
  if (b.dataset.chartZoom) {
    const scroller = b.closest(".panel").querySelector(".chart-scroll");
    const zoom = Number(scroller.dataset.zoom || 1);
    setChartZoom(scroller, b.dataset.chartZoom === "reset" ? 1 : zoom * (b.dataset.chartZoom === "in" ? 1.5 : 1 / 1.5));
    return;
  }
  if (b.dataset.candidateMerge || b.dataset.candidateLearn) {
    const learn = !!b.dataset.candidateLearn;
    const selected = [...b.closest(".review-card").querySelectorAll(".candidate-version:checked")];
    if (selected.length < 2) return;
    const name = b.closest(".review-card").querySelector(".candidate-name").value.trim();
    if (!name) { toast("Choose a combined name first."); return; }
    confirmAction(learn ? "Merge and remember this suffix?" : "Merge selected versions?",
      `${selected.map(v => `“${v.dataset.name}”`).join(", ")} will share a total under “${name}”. ` +
      (learn ? `Remember ${b.dataset.suffix} for ${b.dataset.artist} on future imports. Undo removes this rule.` : "Unselected versions keep their current grouping. You can undo this decision."),
      () => groupAction(learn ? "merge_learn" : "merge_versions", learn ? b.dataset.candidateLearn.split(",").map(Number) : selected.map(v => Number(v.value)), "", name));
    return;
  }
  if (b.dataset.candidateKey) {
    groupAction(b.dataset.candidateAction, [], b.dataset.candidateKey).catch(report); return;
  }
  if (b.dataset.view) {
    if (b.dataset.scopeKind) {
      state.view = b.dataset.view;
      state.filter = {kind:b.dataset.scopeKind, id:b.dataset.scopeId, name:b.dataset.scopeName};
      state.q = "";
      state.offset = 0;
      $("#detail-dialog").close();
      load();
    } else {
      changeView(b.dataset.view);
    }
    return;
  }
  if (b.dataset.browse) {
    state.view = b.dataset.browse;
    state.filter = b.dataset.entity ? {
      kind: b.dataset.entity, id: b.dataset.id, name: b.dataset.name,
    } : null;
    state.mode = b.dataset.mode;
    state.q = "";
    state.offset = 0;
    state.selected.clear();
    if (b.dataset.all === "true") state.period = "all";
    $("#detail-dialog").close();
    load();
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
    try { localStorage.setItem("listening-version-mode", state.mode); } catch (_) {}
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
  if (b.dataset.suggestArtistA && b.dataset.suggestArtistB) {
    const ids = [b.dataset.suggestArtistA, b.dataset.suggestArtistB];
    const name = b.dataset.suggestName;
    confirmAction("Merge these artist identities?",
      "This proposed match is not automatic. Only approve if they are the same artist. Matching songs and albums will combine; Undo restores the original identities.",
      () => groupAction("merge_artists", ids, null, name));
    return;
  }
  if (b.id === "merge-artists") {
    const entries = [...state.selected.entries()];
    const name = $("#artist-name").value.trim() || entries[0][1];
    confirmAction("Merge artist identities?",
      entries.map(e => "“" + e[1] + "”").join(", ") +
      " will be counted as one artist called “" + name + "”. Identical songs and albums will also combine. Undo restores the original grouping.",
      () => groupAction("merge_artists", entries.map(e => e[0]), null, name));
    return;
  }
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
  if (event.target.classList.contains("candidate-name-choice")) {
    const select = event.target;
    const input = select.closest(".review-card").querySelector(".candidate-name");
    if (select.value === "custom") {
      input.dataset.edited = "true";
      input.focus(); input.select();
    } else {
      input.value = select.selectedOptions[0].dataset.name;
      delete input.dataset.edited;
      select.dataset.chosen = select.value;
    }
  }
  if (event.target.classList.contains("candidate-version")) {
    const card = event.target.closest(".review-card");
    const selected = card.querySelectorAll(".candidate-version:checked").length;
    const total = card.querySelectorAll(".candidate-version").length;
    const button = card.querySelector("[data-candidate-merge]");
    button.disabled = selected < 2;
    button.textContent = selected < 2 ? "Merge selected" : `Merge ${selected} selected`;
    updateCandidateName(card);
    const learn = card.querySelector("[data-candidate-learn]");
    if (learn) learn.disabled = selected !== total;
  }
  if (event.target.classList.contains("group-select")) {
    const el = event.target;
    if (el.checked) state.selected.set(el.dataset.id, el.dataset.name);
    else state.selected.delete(el.dataset.id);
    $("#selected-count").textContent = `${state.selected.size} selected`;
    const button = $("#merge") || $("#merge-artists");
    if (button) button.disabled = state.selected.size < 2;
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
  if (event.target.classList.contains("candidate-name")) {
    event.target.dataset.edited = "true";
    event.target.closest(".review-card").querySelector(".candidate-name-choice").value = "custom";
  }
  if (event.target.id !== "search") return;
  clearTimeout(searchTimer);
  const value = event.target.value;
  searchTimer = setTimeout(async () => {
    state.q = value;
    state.offset = 0;
    await load({historyMode:"replace"});
    const search = $("#search");
    if (search) {
      search.focus();
      search.setSelectionRange?.(value.length, value.length);
    }
  }, 300);
});
$("#source").onclick = () => {
  state.source = state.source === "vinyl" ? "all" : "vinyl";
  state.offset = 0;
  load();
};
$("#reset-filters").onclick = () => {
  state.period = "all";
  state.source = "all";
  state.start = state.end = state.q = "";
  state.filter = null;
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
function closeDetail() {
  $("#detail-dialog").close();
  detailSerial++;
  if (history.state?.detail) history.back();
}
$("#close-detail").onclick = closeDetail;
$("#detail-dialog").addEventListener("cancel", event => {event.preventDefault(); closeDetail();});
$("#confirm-cancel").onclick = () => $("#confirm-dialog").close();
$("#theme").onclick = () => {
  const next =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("listening-theme", next);
  } catch (_) {}
  updateThemeColour();
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

if ($("#logout")) $("#logout").onclick = async () => {
  try {
    await api("logout", {}, { method: "POST", body: "{}" });
    location.assign(new URL("login", document.baseURI));
  } catch (error) { report(error); }
};

let modalScrollY = 0;
function lockModalBackground() {
  const open = !!document.querySelector("dialog[open]");
  if (open && !document.body.classList.contains("modal-open")) {
    modalScrollY = window.scrollY;
    document.body.style.top = `-${modalScrollY}px`;
    document.body.classList.add("modal-open");
  } else if (!open && document.body.classList.contains("modal-open")) {
    document.body.classList.remove("modal-open");
    document.body.style.top = "";
    window.scrollTo(0, modalScrollY);
  }
}
for (const dialog of document.querySelectorAll("dialog")) {
  new MutationObserver(lockModalBackground).observe(dialog, { attributes: true, attributeFilter: ["open"] });
  dialog.addEventListener("close", lockModalBackground);
}

const palettes = ["violet", "ocean", "forest", "rose", "amber"];
try { const saved = localStorage.getItem("listening-palette"); document.documentElement.dataset.palette = palettes.includes(saved) ? saved : "violet"; } catch (_) {}
$("#palette").value = document.documentElement.dataset.palette;
$("#palette").onchange = () => {
  document.documentElement.dataset.palette = $("#palette").value;
  try { localStorage.setItem("listening-palette", $("#palette").value); } catch (_) {}
  updateThemeColour();
};
if (window.top === window && "serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register(new URL("sw.js", document.baseURI)).catch(() => {});
}

function updateThemeColour() {
  $("meta[name=theme-color]").content = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
}
updateThemeColour();
let installPrompt;
window.addEventListener("beforeinstallprompt", event => {
  if (window.top !== window) return;
  event.preventDefault(); installPrompt = event;
  $("#install-app").hidden = false;
});
$("#install-app").onclick = async () => {
  if (!installPrompt) return;
  await installPrompt.prompt();
  const choice = await installPrompt.userChoice;
  $("#install-app").hidden = true;
  installPrompt = null;
};
window.addEventListener("appinstalled", () => { $("#install-app").hidden = true; installPrompt = null; });

// Zoom around the gesture's midpoint so the same part of the timeline stays
// under the fingers. One-finger scrolling remains native to the browser.
function setChartZoom(scroller, requested, anchor = scroller.clientWidth / 2, contentPoint = null) {
  const chart = scroller.querySelector(".bar-chart");
  const oldWidth = chart.getBoundingClientRect().width;
  const point = contentPoint ?? (scroller.scrollLeft + anchor) / oldWidth;
  const zoom = Math.max(1, Math.min(8, requested));
  scroller.dataset.zoom = zoom;
  chart.style.setProperty("--zoom", zoom);
  scroller.scrollLeft = point * chart.getBoundingClientRect().width - anchor;
  const tools = scroller.closest(".panel").querySelector(".chart-zoom-buttons");
  if (tools) {
    tools.querySelector('[data-chart-zoom="out"]').disabled = zoom <= 1;
    tools.querySelector('[data-chart-zoom="in"]').disabled = zoom >= 8;
  }
}
const chartGestures = new WeakMap();
function touchGeometry(event, scroller) {
  const [a, b] = event.touches;
  return {
    distance: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
    anchor: (a.clientX + b.clientX) / 2 - scroller.getBoundingClientRect().left,
  };
}
document.addEventListener("touchstart", event => {
  const scroller = event.target.closest(".chart-scroll");
  if (!scroller || event.touches.length !== 2) return;
  const geometry = touchGeometry(event, scroller);
  const width = scroller.querySelector(".bar-chart").getBoundingClientRect().width;
  chartGestures.set(scroller, {
    distance: Math.max(1, geometry.distance),
    zoom: Number(scroller.dataset.zoom || 1),
    point: (scroller.scrollLeft + geometry.anchor) / width,
  });
  scroller.dataset.gestureUntil = "Infinity";
  if (event.cancelable) event.preventDefault();
}, { passive: false });
document.addEventListener("touchmove", event => {
  const scroller = event.target.closest(".chart-scroll");
  const gesture = scroller && chartGestures.get(scroller);
  if (!gesture || event.touches.length !== 2) return;
  const geometry = touchGeometry(event, scroller);
  setChartZoom(scroller, gesture.zoom * geometry.distance / gesture.distance, geometry.anchor, gesture.point);
  if (event.cancelable) event.preventDefault();
}, { passive: false });
function endChartGesture(event) {
  const scroller = event.target.closest(".chart-scroll");
  if (scroller && chartGestures.has(scroller) && event.touches.length < 2) {
    chartGestures.delete(scroller);
    scroller.dataset.gestureUntil = String(Date.now() + 400);
  }
}
document.addEventListener("touchend", endChartGesture);
document.addEventListener("touchcancel", endChartGesture);
// Touch scrolling/pinching must not open a bar as a synthetic tap afterwards.
document.addEventListener("click", event => {
  const scroller = event.target.closest(".chart-scroll");
  if (scroller && Number(scroller.dataset.gestureUntil || 0) > Date.now()) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
}, true);
