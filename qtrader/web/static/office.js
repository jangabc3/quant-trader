"use strict";
// AI 사무실: 회의 참석자를 픽셀 사무실에 그리고, 진행 상황에 맞춰 움직이게 한다.
// 일하는 사람은 자료실·동료 책상을 오가고, 보고서가 끝나면 서류가 다음 담당자에게 날아간다.

const W = 640, H = 360, S = 2;
const office = { roles: [], stages: [], run: null, selected: null, poll: null, frame: 0, thread: null,
  ents: {}, papers: [], seenHandoffs: 0, chatBusy: false };

const ROOMS = [
  { id: "A", x: 8, y: 22, w: 300, h: 150, floor: "#1c2533", label: "분석팀", en: "RESEARCH" },
  { id: "B", x: 320, y: 22, w: 116, h: 150, floor: "#1a2230", label: "검증실", en: "FACT CHECK" },
  { id: "C", x: 448, y: 22, w: 184, h: 150, floor: "#1f2433", label: "토론실", en: "DEBATE" },
  { id: "D", x: 8, y: 186, w: 180, h: 166, floor: "#1b2431", label: "트레이딩 데스크", en: "EXECUTION" },
  { id: "E", x: 200, y: 186, w: 250, h: 166, floor: "#1d2232", label: "리스크 위원회", en: "RISK" },
  { id: "F", x: 462, y: 186, w: 170, h: 166, floor: "#221f2c", label: "대표실", en: "PORTFOLIO" },
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
  teal: ["#2b5d6b", "#1e1e26"], navy: ["#2c3e66", "#3a2a1e"], amber: ["#8a6a3a", "#1e1e22"], rose: ["#7a4a5e", "#4a2e1e"],
  green: ["#2f6a4e", "#2a2018"], green2: ["#355e57", "#151515"], red: ["#7a3438", "#151515"], purple: ["#4e3f73", "#241a2a"],
  slate: ["#3b4656", "#0e1116"], orange: ["#8a5232", "#3a2010"], gray: ["#5b636e", "#2a2a2a"], blue: ["#30508a", "#141a26"],
  gold: ["#2a2f3b", "#d8d2c4"], pink: ["#6e5a8a", "#2a1a10"], white: ["#c9d1db", "#3a2a1e"],
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
  px(0, 0, W, H, "#0d1118");
  for (const r of ROOMS) {
    px(r.x, r.y, r.w, r.h, r.floor);
    for (let y = r.y + 14; y < r.y + r.h; y += 16) px(r.x, y, r.w, 1, "rgba(255,255,255,.025)");
    for (let x = r.x; x < r.x + r.w; x += 16) px(x, r.y + 14, 1, r.h - 14, "rgba(255,255,255,.02)");
    // 위쪽 벽 + 간접 조명
    px(r.x, r.y, r.w, 13, "#121822"); px(r.x, r.y + 13, r.w, 1, "rgba(201,162,39,.55)");
    g.fillStyle = "rgba(120,170,255,.05)"; g.fillRect(r.x, r.y + 14, r.w, 10);
    // 유리벽
    px(r.x - 2, r.y - 2, r.w + 4, 2, "#3a4a60"); px(r.x - 2, r.y + r.h, r.w + 4, 2, "#3a4a60");
    px(r.x - 2, r.y, 2, r.h, "#3a4a60"); px(r.x + r.w, r.y, 2, r.h, "#3a4a60");
    px(r.x, r.y + r.h - 1, r.w, 1, "rgba(255,255,255,.06)");
  }
}

function plant(x, y) {
  px(x, y + 8, 8, 6, "#3a3f48"); px(x - 1, y + 7, 10, 2, "#2a2e36");
  px(x + 2, y, 4, 8, "#2f6a46"); px(x - 1, y + 2, 4, 4, "#3a7d52"); px(x + 5, y + 1, 4, 4, "#3a7d52");
}

function shelf(x, y, w) { // 정돈된 자료 캐비닛
  px(x, y, w, 20, "#2a3240"); px(x, y + 9, w, 1, "#1a202a");
  for (let i = 3; i < w - 6; i += 12) { px(x + i, y + 2, 9, 6, "#384356"); px(x + i, y + 11, 9, 6, "#384356"); px(x + i + 3, y + 4, 3, 1, "#6b7890"); px(x + i + 3, y + 13, 3, 1, "#6b7890"); }
}

function cabinet(x, y) { px(x, y, 14, 26, "#3a4456"); for (let i = 0; i < 3; i++) { px(x + 1, y + 2 + i * 8, 12, 6, "#46526a"); px(x + 5, y + 4 + i * 8, 4, 1, "#9aa6ba"); } }

function monitor(x, y, on, t, kind = 0) {
  px(x, y, 16, 11, "#0b0f15"); px(x + 1, y + 1, 14, 8, on ? "#0c2038" : "#141a22");
  if (on) {
    for (let i = 0; i < 12; i++) {
      const h = kind ? ((i * 7 + t) % 6) : ((i * 3 + t) % 6);
      px(x + 2 + i, y + 7 - h, 1, kind ? h : 1, i % 3 ? "#5fb4ff" : "#ffd34d");
    }
    g.fillStyle = "rgba(95,180,255,.08)"; g.fillRect(x - 3, y + 11, 22, 6);
  }
  px(x + 7, y + 11, 2, 2, "#2a313c");
}

function desk(x, y, w, on, t, mon = []) {
  px(x, y, w, 9, "#3a4352"); px(x, y + 9, w, 4, "#232a35"); px(x + 1, y + 1, w - 2, 1, "#56627a");
  mon.forEach((dx, i) => monitor(x + dx, y - 10, on, t + i, i % 2));
}

function bigScreen(x, y, w, h, t, live) {
  px(x - 1, y - 1, w + 2, h + 2, "#3a4a60"); px(x, y, w, h, "#070a10");
  if (!live) return;
  const r = office.run;
  const up = (r?.final && ["Buy", "Overweight"].includes(r.final.rating));
  let prev = h / 2;
  for (let i = 0; i < w - 8; i += 2) {
    const v = h / 2 + 4 + Math.sin((i + t * 2) / 11) * (h / 5) - (up ? i / w * 6 : 0);
    px(x + 4 + i, y + Math.min(prev, v), 2, Math.abs(prev - v) + 1, i > w - 26 ? "#ffd34d" : "#5fb4ff");
    prev = v;
  }
  g.fillStyle = "#c9d1db"; g.font = "bold 7px sans-serif"; g.fillText((r?.symbol || "QTRADER"), x + 4, y + 8);
}

function table(x, y, w, h, c = "#3a4352") { px(x, y, w, h, c); px(x, y + h, w, 4, "#1d232d"); px(x + 1, y + 1, w - 2, 1, "rgba(255,255,255,.12)"); }

// 사람
const TIE = { chart: "#5fb4ff", fund: "#5fb4ff", earn: "#5fb4ff", news: "#5fb4ff", flow: "#5fb4ff", checker: "#e8e0d0",
  bull: "#f0534e", bear: "#4c8dff", lead: "#c9a227", trader: "#3fe07a", aggr: "#f08a5e", neut: "#c7d0d9", cons: "#6aa0ff",
  pm: "#c9a227", assistant: "#ff9fb8" };
function person(e, t) {
  const [shirt, hair] = LOOKS[e.look] || LOOKS.gray;
  const x = e.x, y = e.y;
  const walk = e.moving ? t % 2 : 0;
  const working = e.status === "working";
  const bob = working && !e.moving ? (t % 2) : 0;
  const top = y - 22 * S + bob;
  const p = (dx, dy, w, h, c) => px(x + dx * S, top + dy * S, w * S, h * S, c);
  px(x - 6 * S, y - 2, 12 * S, 3, "rgba(0,0,0,.35)");
  p(-4, 18, 3, walk ? 2 : 3, "#161a22"); p(1, 18, 3, walk ? 3 : 2, "#161a22");
  p(-5, 9, 10, 9, shirt); p(-5, 9, 10, 1, "rgba(255,255,255,.12)");
  p(-1, 9, 2, 1, "#e8ecf2"); p(-1, 10, 2, 4, TIE[e.id] || "#c7d0d9");
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
  e.status = e.id === "assistant" ? (window.chatBusy ? "working" : "idle") : (office.run?.agents?.[e.id]?.status || "idle");
  const atHome = Math.abs(e.x - seat.home[0]) < 1 && Math.abs(e.y - seat.home[1]) < 1;
  if (e.status === "working" && seat.visits.length && t >= e.next && !e.moving) {
    // 일하는 중: 자료실·동료 책상과 내 자리를 번갈아 오간다
    const going = atHome ? seat.visits[Math.floor(Math.random() * seat.visits.length)] : seat.home;
    e.tx = going[0]; e.ty = going[1]; e.carry = !atHome;
    e.next = t + 8 + Math.floor(Math.random() * 10);
  } else if (e.status !== "working" && !atHome && !e.moving) {
    e.tx = seat.home[0]; e.ty = seat.home[1]; e.carry = e.status === "done";
  }
  const dx = e.tx - e.x, dy = e.ty - e.y, d = Math.hypot(dx, dy);
  if (d > 1) {
    const sp = Math.min(d, 8);
    e.x += dx / d * sp; e.y += dy / d * sp; e.moving = true; e.face = dx < 0 ? -1 : 1;
  } else {
    e.x = e.tx; e.y = e.ty;
    if (e.moving) e.next = Math.max(e.next, t + 6);
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
    p.k += 0.09;
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
  plant(292, 150); plant(616, 330);
  px(24, 198, 140, 14, "#070a10"); px(23, 197, 142, 1, "#3a4a60"); // 시세판
  for (let i = 0; i < 9; i++) px(28 + i * 15, 203, 10, 3, (i + t) % 3 ? "#5fb4ff" : "#ffd34d");
  px(596, 200, 30, 26, "#26405e"); px(596, 212, 30, 1, "#3a4a60"); px(610, 200, 1, 26, "#3a4a60"); // 창문 (야경)
  for (let i = 0; i < 6; i++) px(598 + (i * 7) % 26, 203 + (i * 5) % 20, 2, 2, (t + i) % 5 ? "#ffd34d" : "#26405e");
  table(486, 112, 108, 20); px(500, 116, 14, 9, "#d8dde3"); px(564, 118, 14, 9, "#d8dde3"); px(534, 114, 12, 12, "#c9a227");
  table(262, 282, 128, 22);
  px(474, 324, 52, 9, "#3a4352"); px(474, 333, 52, 4, "#232a35"); // 비서 안내 데스크

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
  if (!running && office.run?.status !== "done") { g.fillStyle = "rgba(0,0,0,.18)"; g.fillRect(0, 0, W, H); }
}

// 말풍선·이름표 (HTML)
function buildOverlay() {
  const ov = $("#office-overlay");
  const all = [...office.roles, { id: "assistant", name: "비서", title: "내 AI 비서" }].filter((r) => SEATS[r.id]);
  ov.innerHTML = ROOMS.map((r) => `<div class="room-label" style="left:${(r.x + 6) / W * 100}%;top:${(r.y + 1) / H * 100}%"><b>${r.label}</b><span>${r.en}</span></div>`).join("") +
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
    if (id === "assistant") text = window.chatBusy ? "확인하는 중..." : (office.run?.status === "running" ? "회의 진행 중입니다" : "무엇이든 물어보세요");
    b.textContent = text;
    b.classList.toggle("hidden", !text);
    b.classList.toggle("working", a?.status === "working" || (id === "assistant" && window.chatBusy));
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
    <div class="note">${r.mode === "precise" ? "정밀" : "빠른"} 회의 · ${Math.floor(sec / 60)}분 ${sec % 60}초 · Claude ${r.calls || 0}회 · 약 ${Math.round((r.cost_usd || 0) * 1400).toLocaleString()}원</div>`;
  renderPlanPop(r);
}

function renderPlanPop(r) {
  const el = $("#plan-pop");
  const f = r.final, p = r.plan;
  if (r.status !== "done" || !f || !p || office.popClosed === r.id) { el.classList.add("hidden"); return; }
  const cur = p.currency === "USD" ? "US" : "KR";
  const color = RATING_COLOR[f.rating];
  const passed = r.checklist?.passed;
  el.innerHTML = `<button class="pp-x" title="닫기">×</button><div class="pp-head" style="--c:${color}"><b>${RATING_KO[f.rating]}</b><span>${esc(r.name || r.symbol)} · 확신도 ${Math.round(f.confidence * 100)}%</span>
      <span class="badge ${passed ? "green" : "red"}">${passed ? "매수 체크리스트 통과" : "매수 체크리스트 미통과"}</span></div>
    <div class="pp-grid">
      <div><span>사는 가격</span><b>${money(p.entry_low, cur)} ~ ${money(p.entry_high, cur)}</b></div>
      <div class="down"><span>손절 (여기서 판다)</span><b>${money(p.stop, cur)}</b><em>-${p.stop_pct}%</em></div>
      <div class="up"><span>익절 (여기서 판다)</span><b>${money(p.take, cur)}</b><em>+${p.take_pct}%</em></div>
      <div><span>추천 수량</span><b>${p.qty.toLocaleString()}주</b><em>${money(p.amount, cur)}</em></div>
      <div class="down"><span>틀리면 최대 손실</span><b>${money(p.max_loss, cur)}</b></div>
      <div class="up"><span>맞으면 예상 이익</span><b>${money(p.max_gain, cur)}</b></div>
    </div><div class="note">보유 기간 ${p.horizon_days}거래일 · 손절·익절가에 닿으면 자동매도(설정) · 아래로 내리면 근거 전체</div>`;
  el.classList.remove("hidden");
}

$("#plan-pop").addEventListener("click", (e) => {
  if (e.target.closest(".pp-x")) { office.popClosed = office.run?.id; $("#plan-pop").classList.add("hidden"); }
  else $("#result").scrollIntoView({ behavior: "smooth" });
});

window.showRunById = (id) => { office.run = null; showRun(id); };

async function loadRefs() {
  try {
    const d = await api("/api/principles");
    $("#refs-body").innerHTML = `<table class="refs"><thead><tr><th>연구·서적</th><th>핵심 발견</th><th>QTrader 적용</th></tr></thead><tbody>
      ${d.references.map((r) => `<tr><td><b>${esc(r.author)}</b><div class="note">${esc(r.title)} · ${esc(r.venue)}</div></td><td>${esc(r.finding)}</td><td>${esc(r.applied)}</td></tr>`).join("")}</tbody></table>
      <p class="note">이 원칙들은 '이기는 확률을 조금 높이고 질 때 작게 지는' 방법입니다. 승률 100%를 보장하는 방법은 학계·업계 어디에도 없습니다.</p>`;
  } catch (e) { $("#refs-body").textContent = e.message; }
}

let lastTs = 0;
function loop(ts) {
  if (ts - lastTs > 70) {
    lastTs = ts;
    office.frame = (office.frame + 1) % 100000;
    draw(); placeOverlay();
    if (office.frame % 10 === 0) renderStages();
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
  if (office.selected === "assistant") $("#qbi-btn").dispatchEvent(new PointerEvent("pointerup"));
  else $("#report").scrollIntoView({ behavior: "smooth", block: "nearest" });
});

(async () => {
  const meta = await api("/api/ai/roles");
  office.roles = meta.roles; office.stages = meta.stages;
  buildOverlay(); updateOverlay(); renderStages(); requestAnimationFrame(loop);
})();
document.addEventListener("tab", (e) => { if (e.detail === "office") { loadRuns(); loadTrack(); loadRefs(); } });
