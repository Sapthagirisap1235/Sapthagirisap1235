// jarvis — minimal test client, forked from live-dj's frontend.
// Talk to JARVIS, hear it back, interrupt it mid-sentence, and ask it to do things
// (open apps, run commands, search the web, control the ambient music).

const $ = (id) => document.getElementById(id);
const orb = $("orb"), statusEl = $("status"), nowEl = $("nowplaying"), txEl = $("transcript");
const music = $("music");

const BARGE_RMS = 0.035;              // raised from 0.02 — reduces false triggers from leaked echo
const BARGE_GRACE_MS = 450;           // ignore loud-mic triggers for this long after speech starts
const BARGE_SUSTAIN_FRAMES = 3;       // require this many consecutive loud frames, not one spike
let ws, audioCtx, workletNode, micStream;
let nextStart = 0;
let activeSources = [];
let speaking = false;
let speakingSince = 0;
let loudStreak = 0;
let duckTimer = null;
let tracks = [];
let queue = [];
let qi = 0;
let ytResults = [];

function setOrb(state) { orb.className = "orb " + state; }       // idle | listening | thinking | speaking
function setStatus(t) { statusEl.textContent = t; }
function addLine(role, text) {
  const p = document.createElement("div");
  p.className = "line " + role;
  p.textContent = (role === "jarvis" ? "jarvis  " : "you  ") + text;
  txEl.appendChild(p); txEl.scrollTop = txEl.scrollHeight;
}

// ---------- music player (driven by JARVIS's play_playlist/play_track/skip/pause tools) ----------
async function loadTracks() {
  try { tracks = await (await fetch("/assets/tracks.json")).json(); } catch { tracks = []; }
}
function startQueue(list) {
  queue = list.length ? list : tracks;
  qi = 0;
  if (queue.length) { music.src = "/assets/" + queue[0].file; music.volume = 1; music.play().catch(() => {}); setNow(queue[0]); }
}
function setNow(t) { nowEl.textContent = t ? `now playing · ${t.title} — ${t.artist || ""}` : ""; }
function handlePlay(cmd) {
  if (cmd.action === "playlist") startQueue(tracks);                       // one vibe (all dream pop here)
  else if (cmd.action === "track") {
    const t = tracks.find((x) => x.title.toLowerCase().includes((cmd.value || "").toLowerCase()));
    startQueue(t ? [t] : tracks);
  } else if (cmd.action === "skip") { qi = (qi + 1) % Math.max(1, queue.length); if (queue[qi]) { music.src = "/assets/" + queue[qi].file; music.play().catch(() => {}); setNow(queue[qi]); } }
  else if (cmd.action === "pause") { music.paused ? music.play().catch(() => {}) : music.pause(); }
}
function duck() {                                                          // music down while JARVIS talks
  music.volume = 0.12;
  if (duckTimer) clearTimeout(duckTimer);
  duckTimer = setTimeout(() => { music.volume = 1; }, 450);
}

// ---------- open_website: server resolves the URL, THIS browser opens it ----------
function openUrl(url) {
  addLine("tool", `→ open ${url}`);
  let popup = null;
  try { popup = window.open(url, "_blank", "noopener"); } catch {}
  if (!popup) {
    // Popup blocked (very likely, since this fires from a websocket message, not
    // a click) — fall back to a real, tappable link so it's still one tap away.
    const p = document.createElement("div");
    p.className = "line tool";
    const a = document.createElement("a");
    a.href = url; a.target = "_blank"; a.rel = "noopener"; a.textContent = `open ${url} →`;
    a.style.color = "inherit";
    p.appendChild(a); txEl.appendChild(p); txEl.scrollTop = txEl.scrollHeight;
  }
}

// ---------- YouTube search (server) + real youtube.com playback (this browser) ----------
// The results list stays on this page so it's scrollable/tappable, but PLAYING a video
// never embeds it here — it opens the actual youtube.com (or the YouTube app on mobile)
// in a new tab/window, same mechanism as openUrl() below.
function showYoutubeResults(results) {
  ytResults = results;
  const panel = $("yt-panel"), list = $("yt-results");
  if (!panel || !list) return;
  list.innerHTML = "";
  results.forEach((v, i) => {
    const item = document.createElement("div");
    item.className = "yt-item";
    item.innerHTML = `
      <img src="${v.thumbnail}" alt="" />
      <div class="yt-meta"><div class="yt-title">${i + 1}. ${v.title}</div><div class="yt-channel">${v.channel}</div></div>
    `;
    item.addEventListener("click", () => playYoutube(String(i + 1)));
    list.appendChild(item);
  });
  panel.classList.add("open");
}
function playYoutube(indexOrId) {
  let id = indexOrId;
  if (/^\d+$/.test(indexOrId)) {
    const r = ytResults[parseInt(indexOrId, 10) - 1];
    if (!r) { addLine("tool", `→ no result #${indexOrId}`); return; }
    id = r.video_id;
  }
  openUrl(`https://www.youtube.com/watch?v=${id}`);
}
function scrollYoutube(direction) {
  const list = $("yt-results");
  if (!list) return;
  const delta = Math.round(list.clientHeight * 0.8) * (direction === "up" ? -1 : 1);
  list.scrollBy({ top: delta, behavior: "smooth" });
}

// ---------- voice playback (24k PCM from the server) ----------
function playVoice(buf) {
  const int16 = new Int16Array(buf);
  const f32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 0x8000;
  const ab = audioCtx.createBuffer(1, f32.length, 24000);
  ab.getChannelData(0).set(f32);
  const src = audioCtx.createBufferSource();
  src.buffer = ab; src.connect(audioCtx.destination);
  const now = audioCtx.currentTime;
  if (nextStart < now) nextStart = now;
  src.start(nextStart); nextStart += ab.duration;
  activeSources.push(src);
  src.onended = () => { activeSources = activeSources.filter((s) => s !== src); if (!activeSources.length) { speaking = false; setOrb("listening"); } };
  speaking = true; speakingSince = performance.now(); loudStreak = 0; setOrb("speaking"); duck();
}
function stopVoice() {                                                     // barge-in
  activeSources.forEach((s) => { try { s.stop(); } catch {} });
  activeSources = []; nextStart = 0; speaking = false; setOrb("listening");
}

// ---------- the live socket ----------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { setStatus("listening…"); setOrb("listening"); };
  ws.onclose = () => { setStatus("the line dropped — tap to reconnect"); setOrb("idle"); };
  ws.onmessage = (evt) => {
    if (typeof evt.data !== "string") { playVoice(evt.data); return; }     // binary = voice
    const m = JSON.parse(evt.data);
    if (m.type === "transcript") { if (m.role === "user") setOrb("thinking"); addLine(m.role, m.text); }
    else if (m.type === "play") handlePlay(m);
    else if (m.type === "tool_call") addLine("tool", `→ ${m.name}(${JSON.stringify(m.args)})`);
    else if (m.type === "interrupted") stopVoice();
    else if (m.type === "error") { setStatus("error: " + m.message); console.error(m.message); }
    else if (m.type === "ping") { try { ws.send(JSON.stringify({ type: "pong" })); } catch {} }
    else if (m.type === "open_url") openUrl(m.url);
    else if (m.type === "youtube_results") showYoutubeResults(m.results || []);
    else if (m.type === "youtube_play") playYoutube(m.index_or_id);
    else if (m.type === "youtube_scroll") scrollYoutube(m.direction);
  };
}

async function startMic() {
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  await audioCtx.audioWorklet.addModule("/pcm-processor.js");
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const source = audioCtx.createMediaStreamSource(micStream);
  workletNode = new AudioWorkletNode(audioCtx, "pcm-processor");
  workletNode.port.onmessage = (e) => {
    const raw = e.data.rms >= BARGE_RMS;
    // Two layers on top of the raw threshold, both aimed at the same problem
    // (JARVIS hearing its own leaked voice and mistaking it for an interruption):
    //   1. A short grace period right after speech starts — the very first
    //      moment of playback is the likeliest spot for a stray click/echo tail.
    //   2. Requiring several consecutive loud frames, not one, so a single
    //      transient spike can't cut off a whole sentence.
    const pastGrace = !speaking || (performance.now() - speakingSince) > BARGE_GRACE_MS;
    loudStreak = (raw && pastGrace) ? loudStreak + 1 : 0;
    const genuineInterrupt = loudStreak >= BARGE_SUSTAIN_FRAMES;
    if (ws && ws.readyState === WebSocket.OPEN && (!speaking || genuineInterrupt)) ws.send(e.data.pcm);
    if (genuineInterrupt && speaking) stopVoice();
  };
  source.connect(workletNode);
  workletNode.connect(audioCtx.destination);                              // keeps the graph alive (silent)
}

async function go() {
  $("talk").disabled = true;
  setStatus("waking jarvis…"); setOrb("thinking");
  await loadTracks();
  await startMic();
  connect();
  $("talk").textContent = "● live";
}
$("talk").addEventListener("click", go);
