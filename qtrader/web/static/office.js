"use strict";
// AI 사무실: 회의 참석자를 픽셀 사무실에 그리고, 진행 상황에 맞춰 움직이게 한다.
// 일하는 사람은 자료실·동료 책상을 오가고, 보고서가 끝나면 서류가 다음 담당자에게 날아간다.

const W = 640, H = 360, S = 2;
const office = { roles: [], stages: [], run: null, selected: null, poll: null, frame: 0, thread: null,
  ents: {}, papers: [], seenHandoffs: 0, chatBusy: false };

const ROOMS = [
  { id: "A", x: 8, y: 22, w: 300, h: 150, floor: "#7b5332", label: "분석팀" },
  { id: "B", x: 320, y: 22, w: 116, h: 150, floor: "#58606b", label: "검증실" },
  { id: "C", x: 448, y: 22, w: 184, h: 150, floor: "#5e3f35", label: "토론실" },
  { id: "D", x: 8, y: 186, w: 180, h: 166, floor: "#6f4c2e", label: "트레이딩 데스크" },
  { id: "E", x: 200, y: 186, w: 250, h: 166, floor: "#4f4a5c", label: "리스크 위원회" },
  { id: "F", x: 462, y: 186, w: 170, h: 166, floor: "#6b4a26", label: "대표실" },
];
// 자리(발 위치)와 일할 때 오가는 지점
const SEATS = {
  chart: { home: [44, 150], desk: true, visits: [[44, 92]] },
  fund: { home: [102, 150], desk: true, visits: [[90, 82], [150, 82]] },
  earn: { home: [160, 150], desk: true, visits: [[180, 82], [130, 90]] },
  news: { home: [218, 150], desk: true, visits: [[240, 84], [200, 92]] },
  flow: { home: [276, 150], desk: true, visits: [[270, 84], [250, 100]] },
  checker: { home: [378, 150], desk: true, visits: [[44, 168], [102, 168], [160, 168], [218, 168], [276, 168], [360, 84]] },
  bull: { home: [470, 144], visits: [[500, 142]] },
  bear: { home: [610, 144], visits: [[580, 142]] },
  lead: { home: [540, 106], visits: [[500, 106], [580, 106]] },
  trader: { home: [98, 318], desk: true, visits: [[60, 300], [140, 300]] },
  aggr: { home: [236, 336], visits: [[260, 316]] },
  neut: { home: [326, 274], visits: [[300, 270], [352, 270]] },
  cons: { home: [416, 336], visits: [[392, 316]] },
  pm: { home: [578, 296], desk: true, visits: [[610, 232], [540, 240]] },
  assistant: { home: [500, 346], visits: [] },
};
const LOOKS = {
  teal: ["#1f8a8a", "#2a2a35"], navy: ["#2f4a8a", "#4a3020"], amber: ["#d08a1f", "#222"], rose: ["#c0507a", "#6a3a20"],
  green: ["#3a9a4a", "#3a2a1a"], green2: ["#2f7a5a", "#1a1a1a"], red: ["#c03a3a", "#1a1a1a"], purple: ["#7a4ab0", "#2a1a30"],
  slate: ["#55606e", "#101418"], orange: ["#e0702a", "#5a2a10"], gray: ["#8a8f96", "#3a3a3a"], blue: ["#3a6ad0", "#1a2030"],
  gold: ["#c9a227", "#e8e0d0"], pink: ["#e07ab0", "#3a2010"], white: ["#d8dde3", "#5a3a20"],
};
const SKIN = "#f2c9a0";
const RATING_COLOR = { Buy: "#f0534e", Overweight: "#f08a5e", Hold: "#8b98a5", Underweight: "#6aa0ff", Sell: "#4c8dff" };
const RATING_KO = { Buy: "매수", Overweight: "비중확대", Hold: "보유", Underweight: "비중축소", Sell: "매도" };

const cv = $("#office");
const g = cv.getContext("2d");
g.imageSmoothingEnabled = false;
const px = (x, y, w, h, c) => { g.fillStyle = c; g.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h)); };

// 배경
function drawRooms() {
  px(0, 0, W, H, "#24180f");
  for (const r of ROOMS) {
    for (let y = r.y; y < r.y + r.h; y += 8) {
      for (let x = r.x + ((y / 8) % 2) * 8; x < r.x + r.w; x += 16) {
        px(x, y, Math.min(16, r.x + r.w - x), Math.min(8, r.y + r.h - y), r.floor);
        px(x, y, 1, Math.min(8, r.y + r.h - y), "rgba(0,0,0,.16)");
      }
      px(r.x, y + 7, r.w, 1, "rgba(0,0,0,.13)");
    }
    px(r.x, r.y, r.w, 12, "#3c2a1e"); px(r.x, r.y + 12, r.w, 2, "#2a1c12");
    px(r.x - 3, r.y - 3, r.w + 6, 3, "#150e09"); px(r.x - 3, r.y + r.h, r.w + 6, 3, "#150e09");
    px(r.x - 3, r.y, 3, r.h, "#150e09"); px(r.x + r.w, r.y, 3, r.h, "#150e09");
  }
}

function plant(x, y) {
  px(x, y + 8, 8, 6, "#8a4a2a"); px(x - 1, y + 7, 10, 2, "#6a3a1a");
  px(x + 2, y, 4, 8, "#2f8a3a"); px(x - 1, y + 2, 4, 4, "#3aa04a"); px(x + 5, y + 1, 4, 4, "#3aa04a");
}

function shelf(x, y, w) {
  px(x, y, w, 22, "#5b3a1e"); px(x, y + 10, w, 2, "#3a2410");
  const cs = ["#c03a3a", "#3a6ad0", "#e0b02a", "#3a9a4a", "#7a4ab0", "#e8e0d0"];
  for (let i = 2; i < w - 3; i += 4) { px(x + i, y + 2, 3, 8, cs[(i + x) % cs.length]); px(x + i, y + 13, 3, 8, cs[(i * 3 + x) % cs.length]); }
}

function cabinet(x, y) { px(x, y, 14, 26, "#8a8f96"); for (let i = 0; i < 3; i++) { px(x + 1, y + 2 + i * 8, 12, 6, "#a4aab2"); px(x + 5, y + 4 + i * 8, 4, 1, "#555"); } }

function monitor(x, y, on, t, kind = 0) {
  px(x, y, 16, 11, "#1b1f24"); px(x + 1, y + 1, 14, 8, on ? "#0f2a1a" : "#20262c");
  if (on) {
    for (let i = 0; i < 12; i++) {
      const h = kind ? ((i * 7 + t) % 6) : ((i * 3 + t) % 6);
      px(x + 2 + i, y + 7 - h, 1, kind ? h : 1, i % 3 ? "#3fe07a" : "#f0534e");
    }
  }
  px(x + 7, y + 11, 2, 2, "#444");
}

function desk(x, y, w, on, t, mon = []) {
  px(x, y, w, 9, "#a0703f"); px(x, y + 9, w, 4, "#5b3a1e"); px(x + 1, y + 1, w - 2, 1, "#b8864f");
  mon.forEach((dx, i) => monitor(x + dx, y - 10, on, t + i, i % 2));
}

function bigScreen(x, y, w, h, t, live) {
  px(x, y, w, h, "#111"); px(x + 2, y + 2, w - 4, h - 4, live ? "#0d1b2a" : "#1a2026");
  if (!live) return;
  let prev = h / 2;
  for (let i = 0; i < w - 8; i += 2) {
    const v = h / 2 + Math.sin((i + t * 2) / 9) * (h / 4) + Math.sin((i + t) / 3) * 2;
    px(x + 4 + i, y + Math.min(prev, v), 2, Math.abs(prev - v) + 1, i > w - 30 ? "#ffd34d" : "#3fe07a");
    prev = v;
  }
}

function table(x, y, w, h, c = "#7a4a2a") { px(x, y, w, h, c); px(x, y + h, w, 4, "#3a2418"); px(x + 1, y + 1, w - 2, 1, "rgba(255,255,255,.18)"); }

// 사람
function person(e, t) {
  const [shirt, hair] = LOOKS[e.look] || LOOKS.gray;
  const x = e.x, y = e.y;
  const walk = e.moving ? t % 2 : 0;
  const working = e.status === "working";
  const bob = working && !e.moving ? (t % 2) : 0;
  const top = y - 22 * S + bob;
  const p = (dx, dy, w, h, c) => px(x + dx * S, top + dy * S, w * S, h * S, c);
  px(x - 6 * S, y - 2, 12 * S, 3, "rgba(0,0,0,.25)");
  p(-4, 18, 3, walk ? 2 : 3, "#2a2a35"); p(1, 18, 3, walk ? 3 : 2, "#2a2a35");
  p(-5, 9, 10, 9, shirt); p(-5, 9, 10, 1, "rgba(255,255,255,.15)");
  const armL = e.moving ? walk : (working ? t % 2 : 0);
  const armR = e.moving ? 1 - walk : (working ? (t + 1) % 2 : 0);
  p(-7, 10 + armL, 2, 6, shirt); p(5, 10 + armR, 2, 6, shirt);
  p(-7, 16, 2, 1, SKIN); p(5, 16, 2, 1, SKIN);
  if (e.carry) { p(5, 12, 4, 5, "#f4efe2"); p(6, 13, 2, 1, "#888"); }
  p(-4, 2, 8, 7, SKIN);
  p(-5, 0, 10, 3, hair); p(-5, 3, 1, 3, hair); p(4, 3, 1, 3, hair);
  const f = e.face < 0 ? -1 : 0;
  if (t % 23 !== 0) { p(-2 + f, 5, 1, 1, "#222"); p(1 + f, 5, 1, 1, "#222"); }
  p(-3, 6, 1, 1, "#f0a0a0"); p(2, 6, 1, 1, "#f0a0a0"); p(-1, 7, 2, 1, "#c0785a");
  if (working && !e.moving && (e.id === "bull" || e.id === "bear") && t % 6 < 3) {
    px(x + 10, top - 6, 3, 8, "#ffd34d"); px(x + 10, top + 4, 3, 3, "#ffd34d"); // 느낌표
  }
  if (e.status === "done") px(x + 12, top + 2, 6, 6, "#3fe07a");
  if (e.status === "error") px(x + 12, top + 2, 6, 6, "#f0534e");
}

function deskFor(id, t, on) {
  const [x, y] = SEATS[id].home;
  if (["chart", "fund", "earn", "news", "flow"].includes(id)) return desk(x - 22, y - 14, 44, on, t, [26]);
  if (id === "checker") return desk(x - 24, y - 14, 48, on, t, [0, 32]);
  if (id === "trader") return desk(x - 40, y - 14, 80, on, t, [0, 17, 47, 64]);
  if (id === "pm") return desk(x - 28, y - 14, 56, on, t, [0]);
}

// 상태 -> 움직임
function entities() {
  const all = [...office.roles, { id: "assistant", name: "비서", title: "내 AI 비서", look: "pink" }];
  for (const r of all) {
    if (!office.ents[r.id] && SEATS[r.id]) {
      const [x, y] = SEATS[r.id].home;
      office.ents[r.id] = { id: r.id, look: r.look, x, y, tx: x, ty: y, moving: false, face: 1, status: "idle", next: 0, carry: false };
    }
  }
  return office.ents;
}

function think(e, t) {
  const seat = SEATS[e.id];
  e.status = e.id === "assistant" ? (office.chatBusy ? "working" : "idle") : (office.run?.agents?.[e.id]?.status || "idle");
  const atHome = Math.abs(e.x - seat.home[0]) < 1 && Math.abs(e.y - seat.home[1]) < 1;
  if (e.status === "working" && seat.visits.length && t >= e.next && !e.moving) {
    // 일하는 중: 자료실·동료 책상과 내 자리를 번갈아 오간다
    const going = atHome ? seat.visits[Math.floor(Math.random() * seat.visits.length)] : seat.home;
    e.tx = going[0]; e.ty = going[1]; e.carry = !atHome;
    e.next = t + 14 + Math.floor(Math.random() * 16);
  } else if (e.status !== "working" && !atHome && !e.moving) {
    e.tx = seat.home[0]; e.ty = seat.home[1]; e.carry = e.status === "done";
  }
  const dx = e.tx - e.x, dy = e.ty - e.y, d = Math.hypot(dx, dy);
  if (d > 1) {
    const sp = Math.min(d, 4.5);
    e.x += dx / d * sp; e.y += dy / d * sp; e.moving = true; e.face = dx < 0 ? -1 : 1;
  } else {
    e.x = e.tx; e.y = e.ty;
    if (e.moving) e.next = Math.max(e.next, t + 10);
    e.moving = false;
    if (Math.abs(e.x - seat.home[0]) < 1 && Math.abs(e.y - seat.home[1]) < 1) e.carry = false;
  }
}

function spawnPapers() {
  const hs = office.run?.handoffs || [];
  for (let i = office.seenHandoffs; i < hs.length; i++) {
    const a = office.ents[hs[i].from], b = office.ents[hs[i].to];
    if (a && b) office.papers.push({ x0: a.x, y0: a.y - 30, x1: b.x, y1: b.y - 30, k: 0, delay: (i - office.seenHandoffs) * 3 });
  }
  office.seenHandoffs = hs.length;
}

function drawPapers() {
  office.papers = office.papers.filter((p) => p.k <= 1);
  for (const p of office.papers) {
    if (p.delay > 0) { p.delay--; continue; }
    p.k += 0.06;
    const x = p.x0 + (p.x1 - p.x0) * p.k, y = p.y0 + (p.y1 - p.y0) * p.k - Math.sin(p.k * Math.PI) * 40;
    px(x - 5, y - 6, 10, 12, "#f4efe2"); px(x - 3, y - 3, 6, 1, "#999"); px(x - 3, y, 6, 1, "#999"); px(x - 3, y + 3, 4, 1, "#999");
  }
}

function draw() {
  const t = office.frame;
  const running = office.run?.status === "running";
  drawRooms();
  shelf(20, 40, 120); shelf(176, 40, 120); cabinet(330, 40); cabinet(346, 40); cabinet(410, 40);
  bigScreen(490, 34, 100, 34, t, !!office.run);
  plant(292, 150); plant(424, 150); plant(616, 40); plant(16, 330); plant(176, 330); plant(434, 196); plant(616, 196);
  px(24, 196, 140, 18, "#111"); // 시세판
  for (let i = 0; i < 9; i++) px(28 + i * 15, 200, 10, 4, (i + t) % 3 ? "#3fe07a" : "#f0534e");
  px(600, 200, 26, 28, "#8fb4d8"); px(600, 213, 26, 2, "#5a3a20"); px(612, 200, 2, 28, "#5a3a20"); // 창문
  table(486, 112, 108, 20); px(500, 116, 14, 9, "#e8e0d0"); px(564, 118, 14, 9, "#e8e0d0"); px(534, 114, 12, 12, "#c9a227");
  table(262, 282, 128, 22, "#6a4a3a");
  px(474, 324, 52, 9, "#c9a06a"); px(474, 333, 52, 4, "#7a5a3a"); // 비서 안내 데스크

  const ents = entities();
  Object.values(ents).forEach((e) => think(e, t));
  spawnPapers();
  const isHome = (e) => Math.abs(e.x - SEATS[e.id].home[0]) <= 1 && Math.abs(e.y - SEATS[e.id].home[1]) <= 1;
  // 자리를 비운 사람의 책상 먼저
  Object.values(ents).forEach((e) => { if (SEATS[e.id].desk && !isHome(e)) deskFor(e.id, t, e.status === "working"); });
  Object.values(ents).sort((a, b) => a.y - b.y).forEach((e) => {
    person(e, t);
    if (SEATS[e.id].desk && isHome(e)) deskFor(e.id, t, e.status === "working");
  });
  drawPapers();
  if (!running && office.run?.status !== "done") { g.fillStyle = "rgba(0,0,0,.12)"; g.fillRect(0, 0, W, H); }
}

// 말풍선·이름표 (HTML)
function buildOverlay() {
  const ov = $("#office-overlay");
  const all = [...office.roles, { id: "assistant", name: "비서", title: "내 AI 비서" }].filter((r) => SEATS[r.id]);
  ov.innerHTML = ROOMS.map((r) => `<div class="room-label" style="left:${(r.x + 6) / W * 100}%;top:${(r.y + 1) / H * 100}%">◆ ${r.label} ◆</div>`).join("") +
    all.map((r) => {
      const hx = SEATS[r.id].home[0];
      const edge = hx < 70 ? "edge-l" : hx > 590 ? "edge-r" : "";
      const narrow = ["chart", "fund", "earn", "news", "flow"].includes(r.id) ? "" : "wide";
      return `<div class="agent ${edge} ${narrow}" data-agent="${r.id}" style="height:${46 / H * 100}%">
        <div class="bubble hidden"></div><div class="hit"></div>
        <div class="tag"><b>${esc(r.name)}</b><span>${esc(r.title)}</span></div></div>`;
    }).join("");
}

function placeOverlay() {
  $$("#office-overlay .agent").forEach((el) => {
    const e = office.ents[el.dataset.agent];
    if (!e) return;
    el.style.left = (e.x / W * 100) + "%";
    el.style.top = ((e.y - 46) / H * 100) + "%";
  });
}

function updateOverlay() {
  $$("#office-overlay .agent").forEach((el) => {
    const id = el.dataset.agent;
    const a = office.run?.agents?.[id];
    const b = el.querySelector(".bubble");
    let text = a?.bubble || "";
    if (id === "assistant") text = office.chatBusy ? "확인하는 중..." : (office.run?.status === "running" ? "회의 진행 중입니다" : "무엇이든 물어보세요");
    b.textContent = text;
    b.classList.toggle("hidden", !text);
    b.classList.toggle("working", a?.status === "working" || (id === "assistant" && office.chatBusy));
    el.classList.toggle("sel", office.selected === id);
  });
}

function renderStages() {
  const r = office.run;
  const st = r?.stages || {};
  $("#stage-line").innerHTML = office.stages.map((s, i) => `<div class="stg ${st[s.key] || "idle"}"><span>${i + 1}</span>${esc(s.name)}</div>`).join("");
  const hud = $("#office-hud");
  if (!r) { hud.innerHTML = `<div>회의 대기 중</div><div class="note">종목을 입력하고 회의를 시작하세요</div>`; return; }
  const end = r.finished_at ? Date.parse(r.finished_at) : Date.now();
  const sec = Math.max(0, Math.round((end - Date.parse(r.started_at)) / 1000));
  const state = r.status === "running" ? `<b class="blink">● 회의 중</b> ${esc(r.stage)}` : r.status === "done" ? "<b>회의 완료</b>" : `<b class="down">오류</b> ${esc(r.error || "")}`;
  hud.innerHTML = `<div>${esc(r.name || r.symbol)} <small>${esc(r.symbol)}</small></div><div>${state}</div>
    <div class="note">${Math.floor(sec / 60)}분 ${sec % 60}초 · 토큰 ${Number((r.tokens_in || 0) + (r.tokens_out || 0)).toLocaleString()}</div>`;
}

let lastTs = 0;
function loop(ts) {
  if (ts - lastTs > 120) {
    lastTs = ts;
    office.frame = (office.frame + 1) % 100000;
    draw(); placeOverlay();
    if (office.frame % 8 === 0) renderStages();
  }
  requestAnimationFrame(loop);
}

// 마크다운 일부(표, 굵게, 목록, 제목)만 안전하게 그린다
function mdLite(text) {
  const lines = esc(text || "").split("\n");
  let out = "", inTable = false;
  for (const ln of lines) {
    const l = ln.trim();
    if (l.startsWith("|")) {
      const cells = l.split("|").slice(1, -1).map((c) => c.trim());
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue;
      if (!inTable) { out += "<table class='md'>"; inTable = true; }
      out += "<tr>" + cells.map((c) => `<td>${c.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")}</td>`).join("") + "</tr>";
      continue;
    }
    if (inTable) { out += "</table>"; inTable = false; }
    const b = l.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    if (/^#{1,4} /.test(l)) out += `<h4>${b.replace(/^#+ /, "")}</h4>`;
    else if (/^[-*•] /.test(l)) out += `<div class="li">• ${b.slice(2)}</div>`;
    else if (l === "---") out += "<hr>";
    else if (b) out += `<p>${b}</p>`;
  }
  return out + (inTable ? "</table>" : "");
}
window.mdLite = mdLite;

function renderReport() {
  const id = office.selected;
  const el = $("#report");
  if (!id) return;
  if (id === "assistant") { el.innerHTML = `<h3>비서</h3><p class="note">아래 대화창에서 비서에게 물어보세요.</p>`; return; }
  const role = office.roles.find((r) => r.id === id);
  const a = office.run?.agents?.[id];
  el.innerHTML = `<h3>${esc(role.name)} · ${esc(role.title)} ${role.web ? "<small>웹 검색</small>" : ""} ${role.deep ? "<small>깊은 추론</small>" : ""}</h3>` +
    (a?.report ? `<div class="report-text">${mdLite(a.report)}</div>` : `<p class="note">${a?.status === "working" ? "작성 중입니다..." : "아직 보고서가 없습니다."}</p>`) +
    (a?.sources?.length ? `<div class="sources"><b>출처</b>${a.sources.slice(0, 10).map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>`).join("")}</div>` : "");
}

async function showRun(id) {
  try {
    const r = await api("/api/ai/run/" + id);
    if (office.run?.id !== r.id) { office.seenHandoffs = r.status === "running" ? 0 : (r.handoffs || []).length; office.papers = []; }
    office.run = r;
    renderStages(); renderReport(); updateOverlay();
    window.renderResult && window.renderResult(r);
    clearTimeout(office.poll);
    if (r.status === "running") office.poll = setTimeout(() => showRun(id), 1500);
    else { loadRuns(); loadTrack(); }
  } catch (e) { $("#meet-stage").textContent = e.message; }
}

async function loadRuns() {
  try {
    const runs = await api("/api/ai/runs?limit=30");
    const cur = office.run?.id;
    $("#meet-runs").innerHTML = `<option value="">지난 회의 보기</option>` + runs.map((r) =>
      `<option value="${r.id}" ${r.id === cur ? "selected" : ""}>${esc(r.trade_date)} ${esc(r.name || r.symbol)} · ${r.status === "running" ? "진행 중" : r.status === "error" ? "오류" : esc(r.rating_ko || "")}</option>`).join("");
    if (!office.run && runs.length) showRun(runs[0].id);
  } catch {}
}

async function loadTrack() {
  const el = $("#track");
  try {
    const t = await api("/api/ai/track");
    const s = t.summary;
    const hr = s.hit_rate == null ? "-" : Math.round(s.hit_rate * 100) + "%";
    el.innerHTML = `<h3>AI 판단 기록 <small>모든 결정과 그 뒤 실제 가격을 그대로 보여줍니다</small></h3>
      <div class="track-top">
        <div><span class="k">회의 결정</span><b>${s.decisions}건</b></div>
        <div><span class="k">판정 가능</span><b>${s.judged}건</b></div>
        <div><span class="k">방향 적중률</span><b>${hr}</b></div>
        <div><span class="k">매수 의견 평균 수익</span><b class="${cls(s.avg_return_bullish)}">${pct(s.avg_return_bullish)}</b></div>
      </div>
      <table><thead><tr><th>날짜</th><th>종목</th><th>의견</th><th class="r">당시가</th><th class="r">현재가</th><th class="r">수익률</th><th>결과</th></tr></thead><tbody>
      ${t.rows.slice(0, 8).map((x) => `<tr><td>${esc(x.date)}</td><td>${esc(x.name)}</td>
        <td style="color:${RATING_COLOR[x.rating]}">${RATING_KO[x.rating]}</td><td class="r">${money(x.price, x.market)}</td>
        <td class="r">${money(x.last, x.market)}</td><td class="r ${cls(x.return)}">${pct(x.return)}</td>
        <td>${x.correct == null ? "<span class='note'>보유 의견</span>" : x.days === 0 ? "<span class='note'>대기</span>" : x.correct ? "<b class='up'>적중</b>" : "<b class='down'>빗나감</b>"}${x.hit_take ? " · 익절가 도달" : ""}${x.hit_stop ? " · 손절가 도달" : ""}</td></tr>`).join("")
        || `<tr><td colspan="7" class="note">아직 기록이 없습니다.</td></tr>`}</tbody></table>
      <p class="note">어떤 분석도 승률 100%를 보장할 수 없습니다. 결정을 숨김없이 쌓아 어떤 경우에 AI 판단이 믿을 만한지 직접 확인하세요.</p>`;
  } catch (e) { el.innerHTML = `<h3>AI 판단 기록</h3><p class="note">${esc(e.message)}</p>`; }
}

window.startMeeting = async (symbol) => {
  symbol = (symbol || $("#meet-symbol").value).trim();
  if (!symbol) { $("#meet-symbol").focus(); return; }
  $("#meet-symbol").value = symbol;
  if (!confirm(`${symbol} AI 투자 회의를 시작합니다. 참석자 14명이 Claude 를 약 14번 호출합니다(몇 분 걸리고 API 비용이 듭니다). 계속할까요?`)) return;
  try {
    const r = await api("/api/ai/meeting", { method: "POST", body: { symbol } });
    office.selected = "chart";
    office.run = null;
    showRun(r.id);
    $("#office-wrap").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { alert(e.message); }
};
$("#meet-start").onclick = () => window.startMeeting();
$("#meet-symbol").addEventListener("keydown", (e) => { if (e.key === "Enter") window.startMeeting(); });
$("#meet-runs").onchange = () => { if ($("#meet-runs").value) { office.run = null; showRun($("#meet-runs").value); } };
$("#office-overlay").addEventListener("click", (e) => {
  const a = e.target.closest(".agent");
  if (!a) return;
  office.selected = a.dataset.agent;
  renderReport(); updateOverlay();
  if (office.selected === "assistant") $("#chat-input").focus();
  else $("#report").scrollIntoView({ behavior: "smooth", block: "nearest" });
});

// 비서 대화
function chatLine(who, html) {
  const log = $("#chat-log");
  log.insertAdjacentHTML("beforeend", `<div class="msg ${who}">${html}</div>`);
  log.scrollTop = log.scrollHeight;
}
$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#chat-input").value.trim();
  if (!text || office.chatBusy) return;
  $("#chat-input").value = "";
  chatLine("me", esc(text));
  office.chatBusy = true; updateOverlay();
  try {
    const r = await api("/api/assistant", { method: "POST", body: { text, thread_id: office.thread } });
    office.thread = r.thread_id;
    const names = { market_overview: "시장 현황", rankings: "순위", quotes: "시세", chart_brief: "차트", portfolio: "내 계좌",
      recent_orders: "주문 내역", ai_decision: "회의 결론", start_meeting: "회의 시작", web_search: "웹 검색" };
    const tools = r.tools.length ? `<div class="note">확인한 것: ${[...new Set(r.tools)].map((t) => names[t] || t).join(", ")}</div>` : "";
    const src = r.sources.length ? `<div class="sources">${r.sources.map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>`).join("")}</div>` : "";
    chatLine("bot", `<div class="report-text">${mdLite(r.answer)}</div>${tools}${src}`);
    if (r.tools.includes("start_meeting")) { office.run = null; loadRuns(); }
  } catch (err) { chatLine("bot", `<span class="down">${esc(err.message)}</span>`); }
  office.chatBusy = false; updateOverlay();
});

(async () => {
  const meta = await api("/api/ai/roles");
  office.roles = meta.roles; office.stages = meta.stages;
  buildOverlay(); updateOverlay(); renderStages(); requestAnimationFrame(loop);
  chatLine("bot", "안녕하세요, 비서입니다. 시장 상황, 내 계좌, 종목 차트를 물어보시거나 \"삼성전자 회의 열어줘\"라고 해 보세요.");
})();
document.addEventListener("tab", (e) => { if (e.detail === "office") { loadRuns(); loadTrack(); } });
