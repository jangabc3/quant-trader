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
  { id: "D", x: 8, y: 186, w: 424, h: 166, floor: "#4f4a5c", label: "트레이딩 데스크" },
  { id: "F", x: 444, y: 186, w: 188, h: 166, floor: "#6b4a26", label: "대표실" },
];
// 자리(발 위치)와 일할 때 오가는 지점. 7명: 분석 3 · 검증 1 · 토론 2 · 대표 1 (트레이딩 데스크는 가격을 볼 때 들르는 곳)
const SEATS = {
  chart: { home: [62, 150], desk: true, visits: [[60, 92], [120, 300], [240, 300]] },
  fund: { home: [158, 150], desk: true, visits: [[120, 82], [190, 82]] },
  news: { home: [254, 150], desk: true, visits: [[230, 84], [280, 92]] },
  checker: { home: [378, 150], desk: true, visits: [[62, 168], [158, 168], [254, 168], [360, 84]] },
  bull: { home: [480, 144], visits: [[510, 142]] },
  bear: { home: [600, 144], visits: [[570, 142]] },
  pm: { home: [538, 300], desk: true, visits: [[600, 240], [480, 250], [360, 300]] },
};
const LOOKS = {
  teal: ["#1f8a8a", "#2a2a35"], navy: ["#2f4a8a", "#4a3020"], amber: ["#d08a1f", "#222"], rose: ["#c0507a", "#6a3a20"],
  green: ["#3a9a4a", "#3a2a1a"], green2: ["#2f7a5a", "#1a1a1a"], red: ["#c03a3a", "#1a1a1a"], purple: ["#7a4ab0", "#2a1a30"],
  slate: ["#55606e", "#101418"], orange: ["#e0702a", "#5a2a10"], gray: ["#8a8f96", "#3a3a3a"], blue: ["#3a6ad0", "#1a2030"],
  gold: ["#c9a227", "#e8e0d0"], pink: ["#e07ab0", "#3a2010"], white: ["#d8dde3", "#5a3a20"],
};
const SHORT = { chart: "기술·타이밍", fund: "재무·실적", news: "뉴스·수급", checker: "팩트체크", bull: "강세", bear: "약세", pm: "최종 결정" };
const ANALYSTS = ["chart", "fund", "news"];
const MEETING_KRW = "400~700원";  // 7명 · 빠른 모델 · 웹 검색 5번까지 (실제 비용은 회의 화면 위에 나온다)
const SKIN = "#f2c9a0";
const RATING_COLOR = { Buy: "#f04452", Overweight: "#f08a5e", Hold: "#8b98a5", Underweight: "#5a9bff", Sell: "#3182f6" };
const RATING_KO = { Buy: "매수", Overweight: "비중확대", Hold: "보류", Underweight: "비중축소", Sell: "매도" };

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
  if (ANALYSTS.includes(id)) return desk(x - 30, y - 14, 60, on, t, [8, 34]);
  if (id === "checker") return desk(x - 24, y - 14, 48, on, t, [0, 32]);
  if (id === "pm") return desk(x - 32, y - 14, 64, on, t, [8, 40]);
}

// 상태 -> 움직임
function entities() {
  const all = office.roles;
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
  plant(292, 150); plant(424, 150); plant(616, 40); plant(16, 330); plant(410, 330); plant(456, 330); plant(616, 196);
  // 트레이딩 데스크: 벽 시세판 + 차트 화면 (차트봇·대표가 가격을 볼 때 들른다)
  px(24, 196, 392, 18, "#111");
  for (let i = 0; i < 25; i++) px(28 + i * 15.5, 200, 10, 4, (i * 7 + t) % 5 > 1 ? "#3fe07a" : "#f0534e");
  for (let i = 0; i < 25; i++) px(28 + i * 15.5, 207, 7, 3, "#5a6a7a");
  bigScreen(40, 228, 150, 40, t + 9, true); bigScreen(232, 228, 150, 40, t + 21, true);
  desk(60, 300, 120, false, t, [10, 40, 70, 98]); desk(196, 300, 120, false, t + 3, [10, 40, 70, 98]);
  px(600, 200, 26, 28, "#8fb4d8"); px(600, 213, 26, 2, "#5a3a20"); px(612, 200, 2, 28, "#5a3a20"); // 창문
  table(486, 112, 108, 20); px(500, 116, 14, 9, "#e8e0d0"); px(564, 118, 14, 9, "#e8e0d0"); px(534, 114, 12, 12, "#c9a227");

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
  const all = office.roles.filter((r) => SEATS[r.id]);
  ov.innerHTML = ROOMS.map((r) => `<div class="room-label" style="left:${(r.x + 6) / W * 100}%;top:${(r.y + 1) / H * 100}%">${r.label}</div>`).join("") +
    all.map((r) => {
      const hx = SEATS[r.id].home[0];
      const edge = hx < 70 ? "edge-l" : hx > 590 ? "edge-r" : "";
      const narrow = ANALYSTS.includes(r.id) ? "" : "wide";
      return `<div class="agent ${edge} ${narrow}" data-agent="${r.id}" style="height:${46 / H * 100}%">
        <div class="bubble hidden"></div><div class="hit"></div>
        <div class="tag"><b>${esc(r.name)}</b><span>${esc(SHORT[r.id] || r.title)}</span></div></div>`;
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
    // 겹치지 않게: 일하는 사람과 지금 고른 사람만 말풍선
    const show = a?.status === "working" || office.selected === id || (id === "assistant" && window.chatBusy);
    let text = show ? a?.bubble || "" : "";
    b.textContent = text;
    b.classList.toggle("hidden", !text);
    b.classList.toggle("working", a?.status === "working" || (id === "assistant" && window.chatBusy));
    el.classList.toggle("sel", office.selected === id);
  });
}

function renderStages() {
  const r = office.run;
  if (r?.id === "live") { window.liveHudRefresh && window.liveHudRefresh(); return; } // 실시간 사무실은 따로 그린다
  const st = r?.stages || {};
  $("#stage-line").innerHTML = office.stages.map((s, i) => `<div class="stg ${st[s.key] || "idle"}"><span>${i + 1}</span>${esc(s.name)}</div>`).join("");
  const hud = $("#office-hud");
  if (!r) { hud.innerHTML = `<span class="hud-dot"></span><div><b>회의 대기 중</b><div class="note">종목을 입력하고 회의를 시작하세요</div></div>`; return; }
  const end = r.finished_at ? Date.parse(r.finished_at) : Date.now();
  const sec = Math.max(0, Math.round((end - Date.parse(r.started_at)) / 1000));
  const state = r.status === "running" ? `<b class="blink">● 회의 중</b> ${esc(r.stage)}` : r.status === "done" ? "<b>회의 완료</b>" : `<b class="down">오류</b> ${esc(r.error || "")}`;
  hud.className = "office-hud " + r.status;
  hud.innerHTML = `<span class="hud-dot"></span><div><b>${esc(r.name || r.symbol)}</b> <small>${esc(r.symbol)}</small><div>${state}</div>
    <div class="note">7명 회의 · ${Math.floor(sec / 60)}분 ${sec % 60}초 · Claude ${r.calls || 0}회 · 약 ${Math.round((r.cost_usd || 0) * 1400).toLocaleString()}원</div></div>`;
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
  fillDrawer();
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
    else {
      loadRuns(); loadTrack();
      // 실시간 화면에서 넘어온 회의면, 결과를 잠깐 보여 준 뒤 실시간 화면으로 돌아간다 (끝난 회의에 멈춰 있지 않게)
      if (office.backToLive) {
        office.backToLive = false;
        office.poll = setTimeout(() => { if (state.tab === "office" && office.run?.id === r.id && window.liveOn) { window.liveOn(); toast("회의가 끝나 실시간 화면으로 돌아왔어요", "radar"); } }, 20000);
      }
    }
  } catch (e) {
    // 서버가 잠깐 바쁘거나 응답이 늦어도 멈추지 않고 다시 묻는다
    $("#meet-stage").textContent = "회의 상황을 다시 불러오는 중... (" + e.message + ")";
    clearTimeout(office.poll);
    if (!office.run || (office.run.id === id && office.run.status === "running")) office.poll = setTimeout(() => showRun(id), 4000);
  }
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
    const bull = t.rows.filter((x) => x.rating === "Buy" || x.rating === "Overweight").length;
    const bear = t.rows.filter((x) => x.rating === "Sell" || x.rating === "Underweight").length;
    const holds = t.rows.filter((x) => x.correct == null);
    const graded = t.rows.filter((x) => x.correct != null);
    const since = (x) => x.days === 0 ? `<span class="note">오늘 회의</span>` : `<span class="${cls(x.return)}">${pct(x.return)}</span>`;
    const verdict = (x) => (x.days === 0 ? "<span class='note'>내일부터 채점</span>" : x.correct ? "<b class='up'>맞힘</b>" : "<b class='down'>빗나감</b>")
      + (x.hit_take ? " <span class='tk-tag up'>익절가 도달</span>" : "") + (x.hit_stop ? " <span class='tk-tag down'>손절가 도달</span>" : "");
    el.innerHTML = `<h3>AI 판단 채점표 <small>AI 가 '사라·팔아라' 한 뒤 실제로 가격이 그쪽으로 갔는지 확인해요</small></h3>
      <div class="track-top">
        <div><span class="k">지금까지 회의</span><b>${s.decisions}번</b><em class="note">사라 ${bull} · 보류 ${holds.length} · 팔아라 ${bear}</em></div>
        <div><span class="k">채점한 회의</span><b>${s.judged}번</b><em class="note">사라·팔아라 의견만, 하루 지난 뒤</em></div>
        <div><span class="k">맞힌 비율</span><b>${hr}</b><em class="note">${s.judged ? "채점한 회의 중" : "채점할 회의가 아직 없어요"}</em></div>
        <div><span class="k">'사라' 했던 종목 평균</span><b class="${cls(s.avg_return_bullish)}">${pct(s.avg_return_bullish)}</b><em class="note">회의 때 가격 → 지금</em></div>
      </div>
      ${graded.length ? `<table><thead><tr><th>회의한 날</th><th>종목</th><th>AI 의견</th><th class="r">회의 때 가격</th><th class="r">지금 가격</th><th class="r">그 뒤 변화</th><th>채점</th></tr></thead><tbody>
        ${graded.slice(0, 8).map((x) => `<tr><td>${esc(x.date.slice(5))}</td><td>${esc(x.name)}</td>
          <td style="color:${RATING_COLOR[x.rating]}">${RATING_KO[x.rating]}</td><td class="r">${money(x.price, x.market)}</td>
          <td class="r">${money(x.last, x.market)}</td><td class="r">${since(x)}</td><td>${verdict(x)}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty tk-empty"><i data-lucide="clipboard-list"></i><p>${s.decisions ? `아직 '사라·팔아라' 의견이 없어요` : "아직 회의 기록이 없어요"}</p>
          <span class="note">${s.decisions ? `지금까지 회의 ${s.decisions}번이 모두 보류(사지 않고 지켜봄)였어요. 보류는 맞고 틀림을 세지 않아요.`
            : "AI 회의를 하면 결론과 그때 가격이 여기 남고, 며칠 뒤 실제 가격과 비교해 맞았는지 자동으로 채점해요."}</span></div>`}
      ${holds.length ? `<div class="tk-holds"><b>보류한 종목 <span class="note">${holds.length}개 · 사지 않고 지켜본 뒤 가격이 어떻게 됐는지</span></b>
        <div class="tk-chips">${holds.slice(0, 12).map((x) => `<span class="tk-chip" title="${esc(x.headline || "")}">${esc(x.name)}
          <em>${esc(x.date.slice(5))}</em>${since(x)}</span>`).join("")}</div></div>` : ""}
      <p class="note">시연 회의는 기록에 남지 않아요. 어떤 분석도 승률 100%를 보장할 수 없어요. 결정을 숨김없이 쌓아 어떤 경우에 AI 판단이 믿을 만한지 직접 확인하세요.</p>`;
    icons();
  } catch (e) { el.innerHTML = `<h3>AI 판단 채점표</h3><p class="note">${esc(e.message)}</p>`; }
}

window.startMeeting = async (symbol) => {
  const inp = $("#meet-symbol");
  const typed = inp.value.trim();
  // 검색에서 고른 종목이면 그 코드로, 아니면 쓴 그대로 (서버가 이름을 코드로 바꿔 준다)
  symbol = (symbol || (inp.dataset.symbol && typed === inp.dataset.name ? inp.dataset.symbol : typed)).trim();
  if (!symbol) { inp.focus(); return; }
  const label = typed && symbol === inp.dataset.symbol ? `${typed}(${symbol})` : symbol;
  if (!typed) inp.value = symbol;
  if (!(await ask(`${label} AI 투자 회의를 시작합니다. 7명이 Claude 를 7번 불러요 (2~4분, 약 ${MEETING_KRW} 정도 들어요). 계속할까요?`))) return;
  try {
    const r = await api("/api/ai/meeting", { method: "POST", body: { symbol } });
    office.selected = "chart";
    office.run = null;
    showRun(r.id);
    $("#office-wrap").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { toast(e.message); }
};
$("#meet-start").onclick = () => window.startMeeting();
// 회의 입력칸에도 종목 검색: 'SK하이닉'처럼 써도 고르면 종목 코드로 회의한다
(() => {
  const inp = $("#meet-symbol");
  const wrap = document.createElement("div");
  wrap.className = "srch-wrap";
  inp.parentNode.insertBefore(wrap, inp);
  wrap.appendChild(inp);
  const list = document.createElement("div");
  list.className = "gsearch-list hidden";
  wrap.appendChild(list);
  window.attachSearch(inp, list, (r) => { inp.value = r.name; inp.dataset.symbol = r.symbol; inp.dataset.name = r.name; });
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.defaultPrevented && !e.isComposing) window.startMeeting(); });
})();
$("#meet-runs").onchange = () => { if ($("#meet-runs").value) { office.run = null; showRun($("#meet-runs").value); } };
$("#office-overlay").addEventListener("click", (e) => {
  const a = e.target.closest(".agent");
  if (!a) return;
  office.selected = a.dataset.agent;
  renderReport(); updateOverlay();
  if (office.selected === "assistant") $("#qbi-btn").dispatchEvent(new PointerEvent("pointerup"));
  else openDrawer();
});

// 참석자 보고서: 페이지를 내리지 않고 오른쪽에서 밀려 나오는 창으로
const STATUS_KO = { idle: "대기", working: "작업 중", done: "완료", error: "오류" };
function openDrawer() {
  let d = $("#agent-drawer");
  if (!d) {
    d = document.createElement("aside");
    d.id = "agent-drawer";
    d.innerHTML = `<header><div class="ad-who"></div><div class="ad-nav"><button data-nav="-1" title="이전 사람">‹</button><button data-nav="1" title="다음 사람">›</button>
      <button data-close title="닫기 (Esc)">×</button></div></header><div class="ad-body"></div>`;
    document.body.appendChild(d);
    d.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]")) return closeDrawer();
      const n = e.target.closest("[data-nav]");
      if (n) {
        const ids = office.roles.filter((r) => SEATS[r.id]).map((r) => r.id);
        office.selected = ids[(ids.indexOf(office.selected) + Number(n.dataset.nav) + ids.length) % ids.length];
        renderReport(); updateOverlay();
      }
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  }
  d.classList.add("open");
  fillDrawer();
}
function closeDrawer() { $("#agent-drawer")?.classList.remove("open"); office.selected = null; updateOverlay(); }
function fillDrawer() {
  const d = $("#agent-drawer");
  if (!d || !d.classList.contains("open") || !office.selected) return;
  const role = office.roles.find((r) => r.id === office.selected);
  const a = office.run?.agents?.[office.selected] || {};
  const [shirt] = LOOKS[role.look] || LOOKS.gray;
  d.querySelector(".ad-who").innerHTML = `<span class="ad-ava" style="background:${shirt}">${esc(role.name.slice(0, 1))}</span>
    <div><b>${esc(role.name)}</b><span>${esc(role.title)} · ${esc(ROOMS.find((x) => x.id === (SEATS[role.id] ? roomOf(role.id) : ""))?.label || "")}</span></div>
    <span class="ad-st ${a.status || "idle"}">${STATUS_KO[a.status || "idle"]}</span>`;
  d.querySelector(".ad-body").innerHTML = (a.bubble ? `<div class="ad-bubble">“${esc(a.bubble)}”</div>` : "") + $("#report").innerHTML.replace(/^<h3>.*?<\/h3>/, "");
}
function roomOf(id) {
  const [x, y] = SEATS[id].home;
  return ROOMS.find((r) => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h)?.id;
}

(async () => {
  const meta = await api("/api/ai/roles");
  office.roles = meta.roles; office.stages = meta.stages;
  buildOverlay(); updateOverlay(); renderStages(); requestAnimationFrame(loop);
})();
document.addEventListener("tab", (e) => { if (e.detail === "office") { loadRuns(); loadTrack(); loadRefs(); } });
