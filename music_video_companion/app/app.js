const $ = id => document.getElementById(id);
let initialized = false;
let lastSong = "";
let loading = false;
const base = location.pathname.endsWith("/") ? location.pathname : location.pathname + "/";
async function api(name, payload) {
  const response = await fetch(base + "api/" + name, {
    method: payload === undefined ? "GET" : "POST",
    headers: payload === undefined ? {} : {"Content-Type": "application/json"},
    body: payload === undefined ? undefined : JSON.stringify(payload),
    cache: "no-store"
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}
function notify(message, bad=false) {
  $("message").textContent = message || "";
  $("message").classList.toggle("error", bad);
}
async function task(callback) {
  if (loading) return;
  loading = true;
  try { await callback(); await refresh(); }
  catch(err) { notify(err.message, true); }
  finally { loading = false; }
}
function fillSelect(id, entities, selected) {
  const el = $(id);
  const title = el.options[0].textContent;
  el.replaceChildren(new Option(title, ""));
  for (const item of entities) {
    el.add(new Option((item.name || item.entity_id) + " · " + item.entity_id, item.entity_id));
  }
  if (selected && ![...el.options].some(o => o.value === selected)) {
    el.add(new Option(selected + " (not available)", selected));
  }
  el.value = selected || "";
}
async function initSettings(settings) {
  let entities = [];
  try {
    entities = await api("entities");
    $("device-error").textContent = "";
  } catch (e) {
    $("device-error").textContent = "Home Assistant device lookup failed: " + e.message;
  }
  fillSelect("spotify", entities, settings.spotify_entity);
  fillSelect("cast-entity", entities, settings.cast_entity);
  fillSelect("mute-entity", entities, settings.mute_entity || "");
  $("source").value = settings.source;
  $("audio-control").value = settings.audio_control || "manual";
  updateAudioControl();
  $("auto").checked = !!settings.auto;
  initialized = true;
}
function candidateCard(item) {
  const card = document.createElement("div");
  card.className = "candidate";
  const thumb = document.createElement("img");
  thumb.src = item.thumbnail || "";
  thumb.alt = "Video thumbnail";
  thumb.loading = "lazy";
  const detail = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = item.title;
  const subtitle = document.createElement("p");
  subtitle.className = "small";
  subtitle.textContent = item.channel_title + (item.trusted ? " · Trusted artist channel" : " · Unverified channel");
  const link = document.createElement("a");
  link.href = "https://www.youtube.com/watch?v=" + encodeURIComponent(item.video_id);
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "Inspect on YouTube ↗";
  const row = document.createElement("div");
  const approve = document.createElement("button");
  approve.className = "secondary";
  approve.textContent = "Approve this video";
  approve.onclick = () => task(async () => {
    if (!confirm("Have you personally checked this video is from an official artist, label or VEVO channel?")) return;
    await api("approve", {video_id:item.video_id, verified:true});
    notify("Saved approved video.");
  });
  const trust = document.createElement("button");
  trust.className = "secondary";
  trust.textContent = "Approve + trust channel";
  trust.onclick = () => task(async () => {
    if (!confirm("Trust this exact YouTube channel for future automatic song matches by this artist? Only do this for the artist's official channel.")) return;
    await api("approve", {video_id:item.video_id, verified:true, trust_channel:true});
    notify("Saved video and trusted channel.");
  });
  row.append(approve, trust);
  detail.append(title, subtitle, link, row);
  card.append(thumb, detail);
  return card;
}
function render(data) {
  const track = data.current;
  const trackID = track.artist + " / " + track.title;
  $("connection").textContent = data.error ? "Connection issue" : "Monitoring";
  $("song").textContent = track.title || "No song detected";
  $("artist").textContent = track.artist || "Play Spotify to get started.";
  $("source-state").textContent = track.state || "idle";
  $("position").textContent = track.state === "playing" ? Math.round(track.position || 0) + "s into the track" : "";
  $("approved").textContent = data.approved
    ? "Approved video: " + data.approved.title + " · " + data.approved.channel_title
    : "No approved official video for this song.";
  $("cast").disabled = !data.approved || track.state !== "playing";
  $("sync-status").textContent = data.active
    ? "Video timing: " + (data.active.sync_state || "Starting") +
      " · One-time seeks: " + (data.active.seek_count || 0)
    : "Video timing: inactive";
  $("offset-save").disabled = !data.approved;
  if (document.activeElement !== $("offset")) {
    $("offset").value = data.approved ? (data.approved.offset_seconds || 0) : 0;
  }
  $("stop").disabled = !data.active;
  $("search").disabled = !track.title || !data.has_youtube_key;
  $("search").textContent = data.has_youtube_key ? "Find official candidates" : "Search needs a YouTube Data API key";
  const candidates = $("candidates");
  if (trackID !== lastSong || candidates.dataset.signature !== JSON.stringify(data.candidates)) {
    candidates.replaceChildren(...data.candidates.map(candidateCard));
    candidates.dataset.signature = JSON.stringify(data.candidates);
    lastSong = trackID;
  }
  if (data.error) notify(data.error, true);
  else if (!$("message").classList.contains("error")) $("message").textContent = data.message || "";
}
async function refresh() {
  const data = await api("status");
  if (!initialized) await initSettings(data.settings);
  render(data);
}
$("refresh-devices").onclick = () => task(async () => {
  initialized = false;
  notify("Refreshing Home Assistant media players…");
});
function updateAudioControl() {
  const manual = $("audio-control").value === "manual";
  $("manual-audio-note").hidden = !manual;
  $("automatic-audio-controls").hidden = manual;
}
$("audio-control").onchange = updateAudioControl;
$("save").onclick = () => task(async () => {
  await api("config", {
    source: $("source").value,
    spotify_entity: $("spotify").value,
    cast_entity: $("cast-entity").value,
    mute_entity: $("mute-entity").value,
    audio_control: $("audio-control").value,
    auto: $("auto").checked
  });
  notify("Settings saved.");
});
$("search").onclick = () => task(async () => {
  const result = await api("search", {});
  notify("Retrieved " + result.candidates.length + " candidates. Inspect the uploader before approving.");
});
$("cast").onclick = () => task(async () => {
  await api("cast", {});
  notify("YouTube Cast launch requested.");
});
$("offset-save").onclick = () => task(async () => {
  const value = Number($("offset").value);
  if (!Number.isFinite(value) || $("offset").value.trim() === "") {
    throw new Error("Enter a valid offset in seconds");
  }
  await api("offset", {seconds: value});
  notify("Offset saved. Applies at next launch or Spotify resume.");
});
$("stop").onclick = () => task(async () => {
  await api("stop", {});
  notify("Video stopped.");
});
$("manual-save").onclick = () => task(async () => {
  await api("approve", {manual:true, video_id:$("manual-id").value,
                        verified:$("manual-verified").checked});
  $("manual-id").value = "";
  $("manual-verified").checked = false;
  notify("Manual official-video approval saved.");
});
refresh().catch(e => notify(e.message, true));
setInterval(() => { if (!loading) refresh().catch(e => notify(e.message,true)); }, 4000);
