"use strict";
// AI 사무실: 회의 참석자를 픽셀 사무실에 그리고, 진행 상황에 맞춰 움직이게 한다.
// 일하는 사람은 자료실·동료 책상을 오가고, 보고서가 끝나면 서류가 다음 담당자에게 날아간다.

const W = 640, H = 360, S = 2;
const office = { roles: [], stages: [], run: null, selected: null, poll: null, frame: 0, thread: null,
  ents: {}, papers: [], fx: [], seenHandoffs: 0, chatBusy: false, now: 0, feed: {} };

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
  pm: { home: [538, 300], desk: true, visits: [[600, 244], [480, 250], [306, 280]] },
  // 엔진 봇 2대: 회의 참석자가 아니라 자동매매가 지금 하는 일 (트레이딩 데스크)
  sigbot: { home: [120, 316], desk: true, visits: [[115, 280]] },
  guardbot: { home: [256, 316], desk: true, visits: [[306, 280]] },
};
const BOTS = [{ id: "sigbot", name: "신호봇", title: "사는 신호 감시 (2분마다)", look: "bot1", bot: true },
  { id: "guardbot", name: "지킴봇", title: "손절·익절 감시 (2분마다)", look: "bot2", bot: true }];
// 쉬는 시간에 들르는 곳 (발 위치)
const PROPS = { coffee: [372, 322], window: [596, 246], screen: [210, 280] };
const HALL_Y = 179;  // 위·아래 방 사이 복도: 방을 바꿀 때는 이 줄을 따라 걷는다 (벽을 뚫고 가지 않게)
const WALK = 92;  // 걷는 속도 (화면 점/초)
const HAS_GSAP = !!window.gsap;  // GSAP 을 못 읽으면 예전처럼 한 칸씩 움직인다
const LOOKS = {
  teal: ["#1f8a8a", "#2a2a35"], navy: ["#2f4a8a", "#4a3020"], amber: ["#d08a1f", "#222"], rose: ["#c0507a", "#6a3a20"],
  green: ["#3a9a4a", "#3a2a1a"], green2: ["#2f7a5a", "#1a1a1a"], red: ["#c03a3a", "#1a1a1a"], purple: ["#7a4ab0", "#2a1a30"],
  slate: ["#55606e", "#101418"], orange: ["#e0702a", "#5a2a10"], gray: ["#8a8f96", "#3a3a3a"], blue: ["#3a6ad0", "#1a2030"],
  gold: ["#c9a227", "#e8e0d0"], pink: ["#e07ab0", "#3a2010"], white: ["#d8dde3", "#5a3a20"],
};
const SHORT = { chart: "기술·타이밍", fund: "재무·실적", news: "뉴스·수급", checker: "팩트체크", bull: "강세", bear: "약세", pm: "최종 결정",
  sigbot: "사는 신호 감시", guardbot: "손절·익절 감시" };
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

function plant(x, y) {  // 잎이 천천히 흔들린다
  const sw = Math.round(Math.sin(office.now * 1.3 + x * 0.37) * 1.2);
  px(x, y + 8, 8, 6, "#8a4a2a"); px(x - 1, y + 7, 10, 2, "#6a3a1a");
  px(x + 2 + sw, y, 4, 8, "#2f8a3a"); px(x - 1 + sw, y + 2, 4, 4, "#3aa04a"); px(x + 5 + sw, y + 1, 4, 4, "#3aa04a");
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

// 사람 (GSAP 이 x·y 를 부드럽게 옮기고, 여기서는 걸음·숨쉬기·타이핑·기지개·커피 컵을 그린다)
function person(e, t) {
  const [shirt, hair] = LOOKS[e.look] || LOOKS.gray;
  const x = Math.round(e.x), y = Math.round(e.y);
  const step = e.moving ? Math.floor(e.dist / 7) % 2 : 0;  // 7점 걸을 때마다 발을 바꾼다
  const working = e.status === "working";
  const typing = working && !e.moving && e.home;
  const breathe = !e.moving && !typing ? (Math.sin(office.now * 2.2 + x) > 0.6 ? 1 : 0) : 0;
  const bob = e.moving ? (step ? -2 : 0) : typing ? (t % 4 === 0 ? 1 : 0) : breathe;
  const top = y - 22 * S + bob;
  const p = (dx, dy, w, h, c) => px(x + dx * S, top + dy * S, w * S, h * S, c);
  px(x - 6 * S, y - 2, 12 * S, 3, "rgba(0,0,0,.25)");
  p(-4, 18, 3, step ? 2 : 3, "#2a2a35"); p(1, 18, 3, step ? 3 : 2, "#2a2a35");
  p(-5, 9, 10, 9, shirt); p(-5, 9, 10, 1, "rgba(255,255,255,.15)");
  if (e.idleAct === "stretch") {  // 기지개: 팔을 위로
    p(-7, 3, 2, 7, shirt); p(5, 3, 2, 7, shirt); p(-7, 2, 2, 1, SKIN); p(5, 2, 2, 1, SKIN);
  } else {
    const armL = e.moving ? step : typing ? t % 2 : 0;
    const armR = e.moving ? 1 - step : typing ? (t + 1) % 2 : 0;
    p(-7, 10 + armL, 2, 6, shirt); p(5, 10 + armR, 2, 6, shirt);
    p(-7, 16, 2, 1, SKIN); p(5, 16, 2, 1, SKIN);
  }
  if (e.carry) { p(5, 12, 4, 5, "#f4efe2"); p(6, 13, 2, 1, "#888"); }
  if (e.cup) { p(5, 12, 3, 4, "#f4efe2"); p(5, 12, 3, 1, "#6b4226"); if (t % 10 < 5) p(6, 10, 1, 1, "rgba(255,255,255,.6)"); }
  p(-4, 2, 8, 7, SKIN);
  p(-5, 0, 10, 3, hair); p(-5, 3, 1, 3, hair); p(4, 3, 1, 3, hair);
  const f = e.face < 0 ? -1 : 0;
  const blink = (Math.floor(office.now * 10) + e.seed) % 37 === 0;
  if (!blink) { p(-2 + f, 5, 1, 1, "#222"); p(1 + f, 5, 1, 1, "#222"); }
  p(-3, 6, 1, 1, "#f0a0a0"); p(2, 6, 1, 1, "#f0a0a0"); p(-1, 7, 2, 1, "#c0785a");
  if (working && !e.moving && (e.id === "bull" || e.id === "bear") && t % 6 < 3) {
    px(x + 10, top - 6, 3, 8, "#ffd34d"); px(x + 10, top + 4, 3, 3, "#ffd34d"); // 느낌표
  }
  if (e.status === "done") px(x + 12, top + 2, 6, 6, "#3fe07a");
  if (e.status === "error") px(x + 12, top + 2, 6, 6, "#f0534e");
}

// 엔진 봇: 둥둥 떠 있는 작은 로봇. 일할 때 안테나가 깜빡이고 얼굴 화면에 그래프가 흐른다
function robot(e, t) {
  const x = Math.round(e.x), y = Math.round(e.y);
  const on = e.status === "working";
  const hover = Math.round(Math.sin(office.now * 2.6 + x) * 1.5);
  const top = y - 58 + hover;  // 책상 위로 몸통까지 보이게
  const body = e.look === "bot1" ? "#4f8fe0" : "#3fb07a";
  px(x - 10, y - 2, 20, 3, "rgba(0,0,0,.22)");
  px(x - 1, top - 6, 2, 6, "#9aa6b2");
  px(x - 2, top - 10, 4, 4, on ? (t % 8 < 5 ? "#3fe07a" : "#a8ffcf") : "#55606e");
  px(x - 11, top, 22, 16, "#dfe6ee"); px(x - 11, top + 15, 22, 1, "#aab4c0");
  px(x - 9, top + 2, 18, 12, "#0f1a24");
  if (on && Math.floor(office.now / 3) % 2) {  // 얼굴 화면: 작은 그래프
    for (let i = 0; i < 8; i++) px(x - 8 + i * 2, top + 11 - ((i * 3 + t) % 6), 2, 1 + ((i * 3 + t) % 6), i % 3 ? "#3fe07a" : "#f0534e");
  } else {
    const blink = (Math.floor(office.now * 10) + e.seed) % 41 === 0;
    const ec = on ? "#3fe07a" : "#5a9bff";
    if (!blink) { px(x - 6, top + 5, 3, 3, ec); px(x + 3, top + 5, 3, 3, ec); }
    px(x - 3, top + 10, 6, 1, ec);
  }
  px(x - 9, top + 17, 18, 13, body); px(x - 9, top + 17, 18, 2, "rgba(255,255,255,.25)");
  px(x - 4, top + 21, 8, 5, "#0f1a24"); px(x - 3, top + 22, on && t % 4 < 2 ? 6 : 3, 1, "#3fe07a");
  const aL = on && e.home ? t % 2 : 0, aR = on && e.home ? (t + 1) % 2 : 0;
  px(x - 13, top + 19 + aL, 3, 8, body); px(x + 10, top + 19 + aR, 3, 8, body);
  px(x - 7, top + 31, 14, 3, "#3a4656");
  if (e.status === "done") px(x + 13, top + 2, 6, 6, "#3fe07a");
}

function deskFor(id, t, on) {
  const [x, y] = SEATS[id].home;
  if (ANALYSTS.includes(id)) return desk(x - 30, y - 14, 60, on, t, [8, 34]);
  if (id === "checker") return desk(x - 24, y - 14, 48, on, t, [0, 32]);
  if (id === "pm") return desk(x - 32, y - 14, 64, on, t, [8, 40]);
  if (id === "sigbot" || id === "guardbot") return desk(x - 60, y - 14, 120, on, t, [10, 40, 70, 98]);
}

// 참석자 + 엔진 봇
const roleOf = (id) => office.roles.find((r) => r.id === id) || BOTS.find((r) => r.id === id);
const isBot = (id) => BOTS.some((b) => b.id === id);
// 엔진 봇은 회의와 상관없이 '지금 자동매매' 기록(lv.data, office-live.js)으로 움직인다
function botLive(id) {
  const d = typeof lv !== "undefined" ? lv.data : null;
  if (!d) return { status: "idle", bubble: "", report: "자동매매를 켜면 일해요. '지금 상황'을 누르면 자세히 보여요." };
  const it = d.intraday || {}, open = !!d.schedule?.open;
  if (id === "sigbot") {
    const on = d.auto && open && it.on;
    return { status: on ? "working" : "idle",
      bubble: on ? `후보 ${it.watching || 0}종목 사는 신호 확인 중${it.last_check ? ` · ${it.last_check}` : ""}` : d.auto ? "장이 열리면 신호를 봐요" : "자동매매가 꺼져 있어요",
      report: "### 신호봇 · 사는 신호 감시\n\n2분마다 오늘 후보의 실시간 가격으로 눌림목 반등·거래량 돌파 신호를 봐요. 신호가 뜨면 베어리가 위험 신호를 확인하고, 통과하면 바로 사요 (AI 비용은 그때만 들어요).\n\n"
        + ((it.items || []).map((x) => `- ${x.at || ""} **${x.name || x.symbol}** ${x.signal || ""} · ${x.state}`).join("\n") || "- 오늘은 아직 신호가 없어요") };
  }
  const w = d.watch || [];
  const cur = w.length ? w[Math.floor(office.now / 4) % w.length] : null;
  return { status: w.length && open ? "working" : "idle",
    bubble: cur ? (cur.stop ? `${cur.name || cur.symbol} 손절까지 ${pct(cur.to_stop)} · 익절까지 ${pct(cur.to_take)}` : `${cur.name || cur.symbol} ${pct(cur.pnl)}`) : "가진 주식이 없어요",
    report: "### 지킴봇 · 손절·익절 감시\n\n장중 2분마다 가진 종목의 가격을 손절가·익절가와 비교해 닿으면 바로 팔아요. 이익이 나면 절반 익절하고 손절을 따라 올려요.\n\n"
      + (w.map((x) => `- **${x.name || x.symbol}** ${x.qty}주 · ${pct(x.pnl)}${x.stop ? ` · 손절 ${x.stop.toLocaleString()}` : ""}`).join("\n") || "- 없음") };
}
const agentOf = (id) => office.run?.agents?.[id] || (isBot(id) ? botLive(id) : undefined);

function entities() {
  for (const r of [...office.roles, ...BOTS]) {
    if (!office.ents[r.id] && SEATS[r.id]) {
      const [x, y] = SEATS[r.id].home;
      office.ents[r.id] = { id: r.id, look: r.look, bot: !!r.bot, x, y, moving: false, face: 1, status: "idle", next: 0, carry: false,
        cup: false, idleAct: null, dist: 0, lx: x, ly: y, home: true, seed: Math.floor(Math.random() * 30), nextIdle: 0 };
    }
  }
  return office.ents;
}

// 걷기: GSAP 타임라인으로 (출발은 천천히, 도착은 부드럽게). 위·아래 방을 오갈 때는 복도를 지난다
function walkTo(e, x, y, after) {
  e.tl?.kill();
  const pts = (e.y < HALL_Y) !== (y < HALL_Y) ? [[e.x, HALL_Y], [x, HALL_Y], [x, y]] : [[x, y]];
  e.moving = true;
  if (!HAS_GSAP) { e.path = pts; e.after = after; return; }
  const tl = gsap.timeline({ onComplete: () => { e.moving = false; e.tl = null; after && after(); } });
  let cx = e.x, cy = e.y;
  pts.forEach(([nx, ny], i) => {
    const d = Math.hypot(nx - cx, ny - cy);
    if (d < 0.5) return;
    const ease = pts.length === 1 ? "power1.inOut" : i === 0 ? "power1.in" : i === pts.length - 1 ? "power1.out" : "none";
    tl.to(e, { x: nx, y: ny, duration: Math.max(0.25, d / WALK), ease, onStart: () => { if (Math.abs(nx - e.x) > 1) e.face = nx < e.x ? -1 : 1; } });
    cx = nx; cy = ny;
  });
  e.tl = tl;
}
function stepFallback(e) {  // GSAP 없이: 한 프레임에 몇 점씩
  if (!e.path?.length) return;
  const [nx, ny] = e.path[0], dx = nx - e.x, dy = ny - e.y, d = Math.hypot(dx, dy);
  if (d <= 3) { e.x = nx; e.y = ny; e.path.shift(); if (!e.path.length) { e.moving = false; e.after && e.after(); } return; }
  e.x += dx / d * 3; e.y += dy / d * 3; e.face = dx < 0 ? -1 : 1;
}
const later = (sec, fn) => HAS_GSAP ? gsap.delayedCall(sec, fn) : setTimeout(fn, sec * 1000);
const near = (e, [x, y]) => Math.abs(e.x - x) < 1 && Math.abs(e.y - y) < 1;

// 쉬는 시간: 커피 가지러 가기 · 기지개 · 창밖 보기 (회의 중이 아닐 때, 한 번에 두 명까지)
function idleLife(e) {
  if (performance.now() / 1000 < e.nextIdle) return;
  e.nextIdle = performance.now() / 1000 + 12 + Math.random() * 28;
  const meeting = office.run?.status === "running" && office.run?.id !== "live";
  if (meeting || Object.values(office.ents).filter((x) => x.idleAct).length >= 2) return;
  const home = SEATS[e.id].home;
  if (e.bot) {  // 봇은 가끔 큰 화면으로 미끄러져 가서 시세를 보고 온다
    if (Math.random() < 0.5) return;
    e.idleAct = "screen";
    return walkTo(e, PROPS.screen[0] + (e.id === "sigbot" ? -40 : 40), PROPS.screen[1], () => later(2.5, () => walkTo(e, home[0], home[1], () => (e.idleAct = null))));
  }
  const r = Math.random();
  if (r < 0.45) {
    e.idleAct = "coffee";
    walkTo(e, PROPS.coffee[0] - 16 - (e.seed % 3) * 14, PROPS.coffee[1] + (e.seed % 2) * 8, () => later(2.2, () => { e.cup = true; walkTo(e, home[0], home[1], () => { e.idleAct = null; later(9, () => (e.cup = false)); }); }));
  } else if (r < 0.75) {
    e.idleAct = "stretch";
    later(1.6, () => (e.idleAct = null));
  } else if (e.id === "pm" || e.id === "bull" || e.id === "bear") {
    e.idleAct = "window";
    const w = e.id === "pm" ? PROPS.window : [560, 150];
    walkTo(e, w[0], w[1], () => later(3, () => walkTo(e, home[0], home[1], () => (e.idleAct = null))));
  } else {
    e.face = -e.face;
    later(1.2, () => (e.face = -e.face));
  }
}

function think(e) {
  const seat = SEATS[e.id];
  const prev = e.status;
  e.status = e.id === "assistant" ? (window.chatBusy ? "working" : "idle") : (agentOf(e.id)?.status || "idle");
  if (!HAS_GSAP) stepFallback(e);
  e.dist += Math.hypot(e.x - e.lx, e.y - e.ly); e.lx = e.x; e.ly = e.y;
  e.home = near(e, seat.home);
  if (e.status === "working" && prev !== "working" && e.idleAct) { e.idleAct = null; e.tl?.kill(); e.moving = false; }  // 일이 오면 쉬던 걸 멈춘다
  if (e.moving) return;
  const now = performance.now() / 1000;
  if (e.status === "working") {
    if (seat.visits.length && now >= e.next) {  // 일하는 중: 자료실·동료 책상과 내 자리를 번갈아 오간다
      const going = e.home ? seat.visits[Math.floor(Math.random() * seat.visits.length)] : seat.home;
      e.carry = !e.home && !e.bot; e.cup = false;
      walkTo(e, going[0], going[1], () => { if (near(e, seat.home)) e.carry = false; });
      e.next = now + (e.home ? 1.2 + Math.random() * 1.5 : 3 + Math.random() * 4);
    }
    return;
  }
  if (!e.home && !e.idleAct) { e.carry = e.status === "done" && !e.bot; walkTo(e, seat.home[0], seat.home[1], () => (e.carry = false)); return; }
  if (e.home) idleLife(e);
}

// 서류: 넘겨줄 때 포물선을 그리며 날아가고 빙글 돈다. 도착하면 반짝
function spawnPapers() {
  const hs = office.run?.handoffs || [];
  for (let i = office.seenHandoffs; i < hs.length; i++) {
    const a = office.ents[hs[i].from], b = office.ents[hs[i].to];
    if (!a || !b) continue;
    const p = { x0: a.x, y0: a.y - 30, x1: b.x, y1: b.y - 30, k: 0, done: false, delay: (i - office.seenHandoffs) * 0.18 };
    office.papers.push(p);
    if (HAS_GSAP) gsap.to(p, { k: 1, duration: 0.85, delay: p.delay, ease: "power2.inOut", onComplete: () => { p.done = true; sparkle(p.x1, p.y1); } });
  }
  office.seenHandoffs = hs.length;
}
function sparkle(x, y) {
  const s = { x, y, r: 2, a: 1 };
  office.fx.push(s);
  if (HAS_GSAP) gsap.to(s, { r: 12, a: 0, duration: 0.5, ease: "power2.out", onComplete: () => (s.dead = true) });
  else s.dead = true;
}
function drawPapers() {
  office.papers = office.papers.filter((p) => !p.done);
  for (const p of office.papers) {
    if (!HAS_GSAP) { p.k += 0.06; if (p.k >= 1) { p.done = true; continue; } }
    const k = p.k, x = p.x0 + (p.x1 - p.x0) * k, y = p.y0 + (p.y1 - p.y0) * k - Math.sin(k * Math.PI) * 46;
    g.save(); g.translate(Math.round(x), Math.round(y)); g.rotate(Math.sin(k * Math.PI * 2) * 0.5);
    px(-5, -6, 10, 12, "#f4efe2"); px(-3, -3, 6, 1, "#999"); px(-3, 0, 6, 1, "#999"); px(-3, 3, 4, 1, "#999");
    g.restore();
  }
  office.fx = office.fx.filter((s) => !s.dead);
  for (const s of office.fx) {
    if (s.steam) { g.fillStyle = `rgba(235,235,235,${s.a})`; g.fillRect(Math.round(s.x + Math.sin(s.y / 4) * 1.5), Math.round(s.y), 3, 3); continue; }
    g.strokeStyle = `rgba(255,211,77,${s.a})`; g.lineWidth = 2;
    g.beginPath(); g.arc(s.x, s.y, s.r, 0, Math.PI * 2); g.stroke();
  }
}

// 소품: 커피머신(김), 창문(실제 시각의 하늘), 시계, 시세판·큰 화면(실제 시세)
function coffeeMachine(x, y) {
  px(x, y, 16, 22, "#3a3f46"); px(x + 2, y + 2, 12, 6, "#1b1f24"); px(x + 4, y + 4, 3, 2, "#3fe07a");
  px(x + 5, y + 12, 6, 6, "#f4efe2"); px(x + 5, y + 12, 6, 1, "#6b4226"); px(x - 2, y + 22, 20, 3, "#2a2e34");
  if (HAS_GSAP && performance.now() - (office.lastSteam || 0) > 450) {
    office.lastSteam = performance.now();
    const s = { x: x + 7, y: y + 8, a: 0.55, steam: true };
    office.fx.push(s);
    gsap.to(s, { y: y - 14, a: 0, duration: 1.8, ease: "sine.out", onComplete: () => (s.dead = true) });
  }
}
function windowPane(x, y, w, h) {
  const hr = new Date().getHours();
  const sky = hr >= 7 && hr < 17 ? "#8fb4d8" : hr >= 17 && hr < 19 ? "#e8a06a" : hr >= 5 && hr < 7 ? "#b8a6d8" : "#1b2440";
  px(x, y, w, h, sky);
  if (sky === "#1b2440") { px(x + 4, y + 4, 1, 1, "#fff"); px(x + 18, y + 8, 1, 1, "#fff"); px(x + 10, y + 18, 1, 1, "#ddd"); }
  else px(x + w - 8, y + 3, 5, 5, hr >= 17 ? "#ffb070" : "#fff6c0");
  px(x, y + h / 2, w, 2, "#5a3a20"); px(x + w / 2, y, 2, h, "#5a3a20");
}
function wallClock(x, y) {
  px(x, y, 38, 13, "#111"); px(x, y + 13, 38, 1, "#333");
  const d = new Date();
  g.fillStyle = "#3fe07a"; g.font = "bold 9px ui-monospace, Menlo, monospace"; g.textBaseline = "top";
  g.fillText(`${String(d.getHours()).padStart(2, "0")}${d.getSeconds() % 2 ? ":" : " "}${String(d.getMinutes()).padStart(2, "0")}`, x + 6, y + 2);
}
// 벽 시세판: 오늘 거래대금 상위 종목과 등락률이 흘러간다 (실제 시세, 30초마다 새로)
function tickerBoard(x, y, w, h) {
  px(x, y, w, h, "#0b0e12");
  const rows = office.feed.rank || [];
  if (!rows.length) {
    for (let i = 0; i < 25; i++) px(x + 4 + i * 15.5, y + 4, 10, 4, (i * 7 + office.frame) % 5 > 1 ? "#3fe07a" : "#f0534e");
    return;
  }
  g.save(); g.beginPath(); g.rect(x, y, w, h); g.clip();
  g.font = "bold 9px Pretendard, sans-serif"; g.textBaseline = "middle";
  const parts = rows.map((r) => [`${r.name} `, `${r.change_rate > 0 ? "▲" : r.change_rate < 0 ? "▼" : ""}${Math.abs((r.change_rate || 0) * 100).toFixed(2)}%   `, r.change_rate]);
  const widths = parts.map(([a, b]) => g.measureText(a).width + g.measureText(b).width);
  const total = widths.reduce((a, b) => a + b, 0) || 1;
  let cx = x + 4 - (office.now * 28) % total;
  for (let rep = 0; rep < 3 && cx < x + w; rep++) {
    parts.forEach(([a, b, r], i) => {
      g.fillStyle = "#d9e1e8"; g.fillText(a, cx, y + h / 2);
      g.fillStyle = r > 0 ? "#f0534e" : r < 0 ? "#5a9bff" : "#8b98a5"; g.fillText(b, cx + g.measureText(a).width, y + h / 2);
      cx += widths[i];
    });
  }
  g.restore();
}
// 큰 화면: 실제 지수 장중 흐름 (없으면 움직이는 예시 선)
function indexScreen(x, y, w, h, idx, t) {
  const pts = idx?.spark || [];
  if (pts.length < 2) return bigScreen(x, y, w, h, t, true);
  px(x, y, w, h, "#111"); px(x + 2, y + 2, w - 4, h - 4, "#0d1b2a");
  const vals = pts.map((p) => p.value), lo = Math.min(...vals), hi = Math.max(...vals) || 1;
  const up = (idx.change ?? 0) >= 0, col = up ? "#f0534e" : "#5a9bff";
  g.strokeStyle = col; g.lineWidth = 1.5; g.beginPath();
  vals.forEach((v, i) => {
    const X = x + 4 + (w - 8) * i / (vals.length - 1), Y = y + h - 5 - (h - 16) * (v - lo) / ((hi - lo) || 1);
    i ? g.lineTo(X, Y) : g.moveTo(X, Y);
  });
  g.stroke();
  const lx = x + w - 4, ly = y + h - 5 - (h - 16) * (vals.at(-1) - lo) / ((hi - lo) || 1);
  if (Math.floor(office.now * 2) % 2) px(lx - 2, ly - 2, 4, 4, "#ffd34d");
  g.font = "bold 8px Pretendard, sans-serif"; g.textBaseline = "top"; g.fillStyle = "#d9e1e8";
  g.fillText(`${idx.name} ${Number(idx.price).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}`, x + 5, y + 4);
  g.fillStyle = col; g.fillText(`${up ? "+" : ""}${((idx.change_rate || 0) * 100).toFixed(2)}%`, x + w - 40, y + 4);
}

function draw() {
  const t = office.frame;
  const running = office.run?.status === "running";
  drawRooms();
  shelf(20, 40, 120); shelf(176, 40, 120); cabinet(330, 40); cabinet(346, 40); cabinet(410, 40);
  bigScreen(490, 34, 100, 34, t, !!office.run);
  plant(292, 150); plant(424, 150); plant(616, 40); plant(16, 330); plant(456, 330); plant(616, 196);
  // 트레이딩 데스크: 벽 시세판 + 지수 화면 + 커피머신 (차트봇·대표·봇이 가격을 볼 때, 쉬는 시간에 들른다)
  tickerBoard(24, 196, 392, 18);
  const ix = office.feed.ov?.indices || [];
  indexScreen(40, 228, 150, 40, ix[0], t + 9); indexScreen(232, 228, 150, 40, ix[1], t + 21);
  coffeeMachine(392, 296);
  windowPane(600, 200, 26, 28);
  wallClock(470, 196);
  px(470, 318, 136, 26, "rgba(201,162,39,.14)");  // 대표실 바닥 깔개
  table(486, 112, 108, 20); px(500, 116, 14, 9, "#e8e0d0"); px(564, 118, 14, 9, "#e8e0d0"); px(534, 114, 12, 12, "#c9a227");

  const ents = entities();
  Object.values(ents).forEach((e) => think(e));
  spawnPapers();
  // 자리를 비운 사람의 책상 먼저, 그다음 앞뒤 순서대로 (아래쪽이 앞)
  Object.values(ents).forEach((e) => { if (SEATS[e.id].desk && !e.home) deskFor(e.id, t, e.status === "working"); });
  Object.values(ents).sort((a, b) => a.y - b.y).forEach((e) => {
    e.bot ? robot(e, t) : person(e, t);
    if (SEATS[e.id].desk && e.home) deskFor(e.id, t, e.status === "working");
  });
  drawPapers();
  if (!running && office.run?.status !== "done") { g.fillStyle = "rgba(0,0,0,.10)"; g.fillRect(0, 0, W, H); }
}

// 실제 시세 받아 두기 (사무실 화면이 보일 때만, 30초마다): 시세판·지수 화면
async function loadFeed() {
  if (state.tab !== "office" || document.hidden) return;
  try { office.feed.ov = await api("/api/market/overview"); } catch {}
  try { office.feed.rank = (await api("/api/market/rankings?kind=amount&market=KR&count=12")).items || []; } catch {}
}
setInterval(loadFeed, 30000);
document.addEventListener("tab", (e) => { if (e.detail === "office") loadFeed(); });

// 말풍선·이름표 (HTML)
function buildOverlay() {
  const ov = $("#office-overlay");
  const all = [...office.roles, ...BOTS].filter((r) => SEATS[r.id]);
  ov.innerHTML = ROOMS.map((r) => `<div class="room-label" style="left:${(r.x + 6) / W * 100}%;top:${(r.y + 1) / H * 100}%">${r.label}</div>`).join("") +
    all.map((r) => {
      const hx = SEATS[r.id].home[0];
      const edge = hx < 70 ? "edge-l" : hx > 590 ? "edge-r" : "";
      const narrow = ANALYSTS.includes(r.id) ? "" : "wide";
      return `<div class="agent ${edge} ${narrow} ${r.bot ? "bot" : ""}" data-agent="${r.id}" style="height:${46 / H * 100}%">
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
    const a = agentOf(id);
    const b = el.querySelector(".bubble");
    // 겹치지 않게: 일하는 사람과 지금 고른 사람만 말풍선 (봇은 고를 때만)
    const show = (a?.status === "working" && !isBot(id)) || office.selected === id || (id === "assistant" && window.chatBusy);
    const text = show ? a?.bubble || "" : "";
    if (text !== b.dataset.txt) {
      b.dataset.txt = text;
      b.textContent = text;
      if (text && HAS_GSAP) gsap.fromTo(b, { "--pop": 0.6, opacity: 0 }, { "--pop": 1, opacity: 1, duration: 0.38, ease: "back.out(2.4)" });
    }
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

// 그리기는 화면 주사율(보통 60번/초)대로 부드럽게, 픽셀 애니메이션(모니터·타이핑)은 70ms 박자로
let lastBeat = 0;
function loop(ts) {
  office.now = ts / 1000;
  if (ts - lastBeat > 70) { lastBeat = ts; office.frame = (office.frame + 1) % 100000; if (office.frame % 10 === 0) { renderStages(); updateOverlay(); } }
  if (state.tab === "office" && !document.hidden) { draw(); placeOverlay(); }
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
  const role = roleOf(id);
  const a = agentOf(id);
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
  const role = roleOf(office.selected);
  const a = agentOf(office.selected) || {};
  const [shirt] = LOOKS[role.look] || (role.bot ? [role.look === "bot1" ? "#4f8fe0" : "#3fb07a"] : LOOKS.gray);
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
