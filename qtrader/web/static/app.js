"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const MODE_NAME = { paper: "가상매매", toss_real: "토스증권 실전", kis_demo: "KIS 모의투자", kis_real: "KIS 실전" };
const LIVE = new Set(["toss_real", "kis_real"]);
const MARKET_NAME = { KR: "국내", US: "미국" };
const STRAT_NAME = { trend: "추세추종", momentum: "모멘텀 순위", meanrev: "평균회귀", ai: "AI 투자 회의" };
const SRC_NAME = { toss: "토스증권", kis: "KIS", csv: "CSV" };
const PARAM_NAME = {
  fast: "단기 이평", slow: "장기 이평", vol_lookback: "변동성 기간", target_vol: "목표 변동성(연)",
  lookback: "모멘텀 기간", skip: "최근 제외일", top_k: "보유 종목 수", rebalance_days: "리밸런싱 주기",
  rsi_n: "RSI 기간", entry_rsi: "진입 RSI", exit_rsi: "청산 RSI", bb_n: "볼린저 기간", bb_k: "볼린저 배수",
  max_hold: "최대 보유일",
};
const state = { status: null, config: null, strategies: {}, tab: "mkt", plans: {} };

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "content-type": "application/json", "x-qtrader-token": window.QT_TOKEN, ...(opts.headers || {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  const detail = typeof data.detail === "string" ? data.detail : "";
  if (!res.ok) throw new Error(detail || `HTTP ${res.status}`);
  return data;
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isUS = (m) => m === "US" || m === "USD";
// 미국 가격 보기: 달러 그대로, 또는 지금 환율로 바꾼 원화. 주문창은 늘 달러로 내고 원화는 옆에 함께 보여 준다
const fxView = { krw: false, rate: 0 };
try { fxView.krw = localStorage.getItem("qtrader.fxview") === "krw"; } catch {}
const fmtUsd = (v) => "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 });
const fmtWon = (v) => Number(v).toLocaleString("ko-KR", { maximumFractionDigits: 0 }) + "원";
const money = (v, m) => v == null || isNaN(v) ? "-" : isUS(m)
  ? (fxView.krw && fxView.rate ? fmtWon(v * fxView.rate) : fmtUsd(v)) : fmtWon(v);
// 큰 달러 금액(시가총액 등): 원화 보기면 조·억 원, 아니면 $억
const bigUSD = (v) => v == null || isNaN(v) ? "-" : fxView.krw && fxView.rate ? bigKRW(v * fxView.rate) + "원" : "$" + bigKRW(v);
// 종목 화면 맨 위 큰 가격 (국내는 원, 미국은 고른 통화로 크게 + 다른 통화 작게)
const bigPriceHtml = (px, m) => {
  if (px == null) return "-";
  if (!isUS(m)) return fmtWon(px);
  if (!fxView.rate) return fmtUsd(px);
  return fxView.krw ? `${fmtWon(px * fxView.rate)}<small class="px-alt">${fmtUsd(px)}</small>` : `${fmtUsd(px)}<small class="px-alt">${fmtWon(px * fxView.rate)}</small>`;
};
// 달러 금액 옆에 붙이는 원화 (예: "$12.30 (17,100원)")
const usdWithKrw = (v) => v == null || isNaN(v) ? "-" : fmtUsd(v) + (fxView.rate ? ` (${fmtWon(v * fxView.rate)})` : "");
async function loadFx() {
  try {
    const r = await api("/api/fx");
    if (r.rate && r.rate !== fxView.rate) {
      const first = !fxView.rate;
      fxView.rate = r.rate;
      if (fxView.krw && !first) document.dispatchEvent(new CustomEvent("tab", { detail: state.tab }));  // 새 환율로 다시 그리기
    }
  } catch {}
  drawFxToggle();
}
function drawFxToggle() {
  const b = $("#fx-toggle");
  if (!b) return;
  b.innerHTML = fxView.krw ? `<b>원</b><span>$</span>` : `<span>원</span><b>$</b>`;
  b.title = `미국 주식 가격을 ${fxView.krw ? "원화로 바꿔" : "달러로"} 보여 주는 중${fxView.rate ? ` · 환율 ${fxView.rate.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}원` : ""}. 누르면 바뀌어요`;
  b.classList.toggle("on", fxView.krw);
}
const pct = (v) => v == null || isNaN(v) ? "-" : Math.abs(v) < 0.00005 ? "0.00%" : (v > 0 ? "+" : "") + (v * 100).toFixed(2) + "%";
const cls = (v) => v > 0.00005 ? "up" : v < -0.00005 ? "down" : "";
const bigKRW = (v) => {
  const a = Math.abs(v);
  if (a >= 1e12) return (v / 1e12).toFixed(1) + "조";
  if (a >= 1e8) return Math.round(v / 1e8).toLocaleString() + "억";
  if (a >= 1e4) return Math.round(v / 1e4).toLocaleString() + "만";
  return Math.round(v).toLocaleString();
};

// 종목 아이콘
// - 미국: 오픈소스 nvstly/icons (NYSE·NASDAQ 로고 모음) 을 원본 GitHub 에서 불러온다. 없으면 이니셜로 대체
// - 국내: 공개 로고 모음이 없어 그룹 대표색 + 이니셜 (로고는 각 회사의 상표)
const LOGO_COLORS = ["#3182f6", "#f04452", "#00b386", "#ff8a00", "#8b5cf6", "#0ea5e9", "#e11d74", "#14b8a6", "#f59e0b", "#6366f1"];
const BRAND = [[/^삼성/, "#1428a0"], [/^SK/i, "#ea002c"], [/^LG/i, "#a50034"], [/^현대|^기아/, "#002c5f"], [/카카오/, "#fee500", "#191919"],
  [/NAVER|네이버/i, "#03c75a"], [/^한화/, "#f37321"], [/셀트리온/, "#0068b7"], [/^KB|국민/, "#ffbc00", "#3a3a3a"], [/신한/, "#0046ff"],
  [/^포스코|POSCO/i, "#05507d"], [/^롯데/, "#da291c"], [/^CJ/i, "#ef4123"], [/^GS/i, "#00a8a8"], [/^HD|^HMM/, "#1d4f91"],
  [/하나/, "#009490"], [/우리/, "#0067ac"], [/크래프톤/, "#222"], [/엔씨/, "#2a2a2a"], [/두산/, "#005eb8"]];
function logo(symbol, name, size = 28) {
  const sym = String(symbol).split(":").pop().toUpperCase();
  const nm = String(name || sym);
  let h = 0;
  for (const c of sym) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const b = BRAND.find(([re]) => re.test(nm));
  const bg = b ? b[1] : LOGO_COLORS[h % LOGO_COLORS.length], fg = b?.[2] || "#fff";
  const ch = nm.replace(/^(KODEX|TIGER|KBSTAR|ACE|SOL|RISE|HANARO)\s*/i, "").trim().charAt(0).toUpperCase();
  const st = `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.44)}px`;
  const initial = `<span class="logo" style="${st};background:${bg};color:${fg}">${esc(ch)}</span>`;
  if (/^[A-Z.\-]+$/.test(sym) && !["KOSPI", "KOSDAQ"].includes(sym)) {
    return `<span class="logo img" style="${st};background:${bg};color:${fg}">${esc(ch)}<img src="https://raw.githubusercontent.com/nvstly/icons/main/ticker_icons/${encodeURIComponent(sym)}.png" alt="" loading="lazy" onerror="this.remove()"></span>`;
  }
  return initial;
}

// 숫자가 부드럽게 바뀌는 효과 (CountUp.js) — 토스처럼 짧게(0.5초), 움직임 줄이기 설정이면 바로 바꾼다
function countTo(el, value, opts = {}) {
  if (!el) return;
  const prev = Number(el.dataset.v || 0);
  el.dataset.v = value;
  if (!window.countUp || !isFinite(value) || REDUCED) { el.textContent = opts.format ? opts.format(value) : value; return; }
  const cu = new countUp.CountUp(el, value, { startVal: prev, duration: 0.5, decimalPlaces: opts.decimals ?? 0,
    formattingFn: opts.format, separator: "," });
  cu.error ? (el.textContent = opts.format ? opts.format(value) : value) : cu.start();
}
function icons() { window.lucide && lucide.createIcons(); }

// 모션: 목록이 아래에서 차례로 떠오르게 (움직임 줄이기 설정이면 생략)
// 토스처럼 차분하게: 짧은 거리(최대 8px)·짧은 시간(최대 0.3초), 차례 간격은 전부 합쳐 0.24초를 넘지 않는다
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
function animateIn(sel, opts = {}) {
  if (REDUCED || document.hidden) return;
  const els = (typeof sel === "string" ? [...document.querySelectorAll(sel)] : [...sel]).slice(0, opts.max || 16);
  const y = Math.max(-8, Math.min(8, opts.y ?? 6)), dur = Math.min(opts.duration || 240, 300);
  const step = Math.min(opts.step || 18, els.length > 1 ? 240 / (els.length - 1) : 0);
  // 브라우저 내장 애니메이션: 끝나면 원래 모습으로 돌아가서, 중간에 멈춰도 내용이 숨겨진 채 남지 않는다
  els.forEach((el, i) => el.animate?.([{ opacity: 0, transform: `translateY(${y}px)` }, { opacity: 1, transform: "none" }],
    { duration: dur, delay: i * step, easing: "cubic-bezier(.2,.8,.2,1)", fill: "backwards" }));
}
window.animateIn = animateIn;
// anime.js 로 만든 움직임(막대 채우기·하트 날리기 등)도 길지 않게: 한 번에 최대 0.4초, 움직임 줄이기면 끝 모습으로 바로
if (window.anime) {
  const rawAnime = window.anime;
  window.anime = Object.assign((p) => rawAnime(REDUCED ? { ...p, duration: 0, delay: 0 } : { ...p, duration: Math.min(p.duration ?? 400, 400) }), rawAnime);
}

// 값이 바뀐 칸을 잠깐 빨강/파랑으로 깜빡 (토스증권 시세처럼)
const lastVals = new Map();
function flashChanges(root, sel = "[data-val]") {
  root.querySelectorAll(sel).forEach((el) => {
    const k = el.dataset.key, v = Number(el.dataset.val), old = lastVals.get(k);
    if (old != null && v !== old && !REDUCED) {
      el.classList.remove("flash-up", "flash-down"); void el.offsetWidth;
      el.classList.add(v > old ? "flash-up" : "flash-down");
      el.addEventListener("animationend", () => el.classList.remove("flash-up", "flash-down"), { once: true });
    }
    lastVals.set(k, v);
  });
}
window.flashChanges = flashChanges;

// 상단 탭 아래 움직이는 밑줄
function moveInk() {
  const on = $("nav button.on"), nav = $("nav");
  if (!on || !nav) return;
  let ink = $("#nav-ink");
  if (!ink) { ink = document.createElement("i"); ink.id = "nav-ink"; nav.appendChild(ink); }
  ink.style.width = on.offsetWidth + "px";
  ink.style.transform = `translateX(${on.offsetLeft}px)`;
}
window.addEventListener("resize", moveInk);

function banner(msg, kind = "") {
  const b = $("#banner");
  b.className = "banner " + kind + (msg ? "" : " hidden");
  b.textContent = msg || "";
}

// 화면 아래에 잠깐 뜨는 알림
function toast(msg, icon = "info") {
  let box = $("#toasts");
  if (!box) { box = document.createElement("div"); box.id = "toasts"; document.body.appendChild(box); }
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<i data-lucide="${icon}"></i><span>${esc(msg)}</span>`;
  box.appendChild(el);
  icons();
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 400); }, 3800);
}

function modal(html) {
  $("#modal-body").innerHTML = html;
  $("#modal").classList.remove("hidden");
}
// 브라우저 기본 창(confirm·prompt·alert)은 앱 안 브라우저에서 보이지도 않고 바로 '취소'가 된다.
// 그래서 확인·입력은 모두 화면 안 창으로 묻는다. ask(...) 는 확인=true, 입력이 있으면 입력값(취소=null).
function ask(msg, { ok = "확인", cancel = "취소", danger = false, input = null, value = "", title = "" } = {}) {
  return new Promise((resolve) => {
    let el = $("#ask");
    if (!el) { el = document.createElement("div"); el.id = "ask"; el.className = "ask-back"; document.body.appendChild(el); }
    el.innerHTML = `<div class="ask-box" role="dialog" aria-modal="true">${title ? `<h3>${esc(title)}</h3>` : ""}
      <p>${esc(msg).replace(/\n/g, "<br>")}</p>
      ${input !== null ? `<label class="ask-in">${esc(input)}<input id="ask-in" value="${esc(value)}" autocomplete="off"></label>` : ""}
      <div class="ask-acts">${cancel ? `<button data-a="no">${esc(cancel)}</button>` : ""}<button class="${danger ? "danger" : "primary"}" data-a="yes">${esc(ok)}</button></div></div>`;
    el.classList.remove("hidden");
    const done = (yes) => {
      el.classList.add("hidden");
      document.removeEventListener("keydown", key, true);
      resolve(input !== null ? (yes ? $("#ask-in").value.trim() : null) : yes);
    };
    // 진짜 누름만 받는다: 같은 단추 위에서 눌렀다 뗀 클릭, 또는 키보드(Enter·Space)로 누른 단추.
    // (화면 캡처 도구 등이 보내는 홀로 된 클릭이 '확인'으로 처리돼 자동매매가 켜진 일이 있었다)
    let downOn = null;
    el.onpointerdown = (e) => { downOn = e.target.closest("[data-a]") || (e.target === el ? el : null); };
    const key = (e) => {
      if (e.key === "Escape") { e.preventDefault(); done(false); }
      else if (e.key === "Enter" && document.activeElement?.id === "ask-in") { e.preventDefault(); done(true); }
    };
    document.addEventListener("keydown", key, true);
    el.onclick = (e) => {
      const b = e.target.closest("[data-a]");
      const keyboard = e.detail === 0 && b && document.activeElement === b;
      if (b && (downOn === b || keyboard)) done(b.dataset.a === "yes");
      else if (e.target === el && downOn === el && cancel) done(false);
      downOn = null;
    };
    setTimeout(() => ($("#ask-in") || el.querySelector(danger ? '[data-a="no"]' : '[data-a="yes"]') || el.querySelector("[data-a]"))?.focus(), 30);
  });
}
const tell = (msg) => ask(msg, { cancel: null });
window.ask = ask;
$("#modal-x").onclick = () => $("#modal").classList.add("hidden");
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });

// 탭
function openTab(name) {
  state.tab = name;
  document.body.dataset.tab = name; // 종목 검색은 시장·종목·뉴스에서만
  $$("nav button").forEach((x) => x.classList.toggle("on", x.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + name));
  if (name === "cfg") loadConfig();
  document.dispatchEvent(new CustomEvent("tab", { detail: name }));
  // 화면 전환 애니메이션 (anime.js)
  const sec = $("#tab-" + name);
  moveInk();
  if (sec) animateIn(sec.querySelectorAll(":scope > .panel, :scope > div > .panel, :scope .idx-card"), { y: 6, step: 20, duration: 240 });
  setTimeout(icons, 0);
}
$$("nav button").forEach((b) => b.addEventListener("click", () => openTab(b.dataset.tab)));

// 전체 종목 검색 (위쪽 검색창)
let searchTimer;
$("#gsearch").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const q = $("#gsearch").value.trim();
    const box = $("#gsearch-list");
    if (!q) { box.classList.add("hidden"); return; }
    try {
      const rows = await api("/api/stocks/search?q=" + encodeURIComponent(q));
      box.innerHTML = rows.map((r) => `<div class="gs-item" data-open="${esc(r.symbol)}">${logo(r.symbol, r.name, 24)}<b>${esc(r.name)}</b><span class="note">${esc(r.symbol)} · ${esc(r.market || "")}</span></div>`).join("")
        || `<div class="gs-item note">검색 결과가 없습니다</div>`;
      box.classList.remove("hidden");
    } catch (e) { box.innerHTML = `<div class="gs-item note">${esc(e.message)}</div>`; box.classList.remove("hidden"); }
  }, 180);
});
$("#gsearch").addEventListener("keydown", async (e) => {
  if (e.key === "Enter") {
    // 검색 결과가 아직 바뀌기 전에 Enter 를 누르면 이전 결과가 열리지 않도록 지금 입력으로 다시 찾는다
    clearTimeout(searchTimer);
    const q = $("#gsearch").value.trim();
    if (!q) return;
    try {
      const rows = await api("/api/stocks/search?q=" + encodeURIComponent(q));
      if (rows.length) {
        $("#gsearch-list").classList.add("hidden");
        $("#gsearch").value = "";
        openTab("stock");
        window.openStock(rows[0].symbol);
      }
    } catch {}
  }
  if (e.key === "Escape") $("#gsearch-list").classList.add("hidden");
});
document.addEventListener("click", (e) => {
  const o = e.target.closest("[data-open]");
  if (o) {
    $("#gsearch-list").classList.add("hidden");
    $("#gsearch").value = "";
    openTab("stock");
    window.openStock && window.openStock(o.dataset.open);
    return;
  }
  if (!e.target.closest(".gsearch")) $("#gsearch-list").classList.add("hidden");
});

// 상태 · 내 계좌
async function refresh() {
  try {
    const s = await api("/api/status");
    state.status = s;
    renderStatus(s);
    if (!s.error && state.tab === "dash") {
      const [pos, ords, evs, plans, appr, ov] = await Promise.all([api("/api/positions"), api("/api/orders?limit=80"),
        api("/api/events?limit=80"), api("/api/plans"), api("/api/approvals?status=pending"), api("/api/account/overview")]);
      state.plans = Object.fromEntries(plans.map((p) => [p.symbol, p]));
      renderApprovals(appr);
      window.renderDash && window.renderDash({ positions: pos, orders: ords, events: evs, overview: ov });
      window.loadDashExtras && window.loadDashExtras();
    }
  } catch (e) { banner("서버 응답 오류: " + e.message); }
}

document.addEventListener("click", (e) => {
  if (!e.target.closest("#fx-toggle")) return;
  fxView.krw = !fxView.krw;
  try { localStorage.setItem("qtrader.fxview", fxView.krw ? "krw" : "usd"); } catch {}
  drawFxToggle();
  toast(fxView.krw ? "미국 주식 가격을 지금 환율로 바꾼 원화로 보여 줘요" : "미국 주식 가격을 달러로 보여 줘요", "repeat");
  document.dispatchEvent(new CustomEvent("tab", { detail: state.tab }));  // 지금 화면을 다시 그린다
});
loadFx();
setInterval(loadFx, 60 * 1000);

function renderStatus(s) {
  const mb = $("#mode-badge");
  mb.textContent = MODE_NAME[s.mode] + (s.data_source ? ` · 시세 ${SRC_NAME[s.data_source] || s.data_source}` : "");
  mb.className = "badge " + (LIVE.has(s.mode) ? "red" : s.mode === "kis_demo" ? "amber" : "green");
  const live = LIVE.has(s.mode);
  document.body.classList.toggle("is-live", live);
  $("#live-strip").classList.toggle("hidden", !live);
  if (live) $("#live-strip").innerHTML = `<i data-lucide="flame"></i> 실전매매 중 · ${esc(MODE_NAME[s.mode])} · 주문이 실제 돈으로 나가요 <button data-mode-go="paper">가상매매로 돌아가기</button>`;
  $("#auto-badge").textContent = s.auto_trading ? "자동매매 실행 중" : "자동매매 꺼짐";
  $("#auto-badge").className = "badge " + (s.auto_trading ? "green" : "");
  $("#kill-badge").textContent = s.kill_switch ? "긴급 정지 중" : "긴급 정지 꺼짐";
  $("#kill-badge").className = "badge " + (s.kill_switch ? "red" : "");
  $("#auto-btn").innerHTML = s.auto_trading ? '<i data-lucide="pause"></i> 자동매매 중지' : '<i data-lucide="play"></i> 자동매매 시작';
  $("#kill-btn").innerHTML = s.kill_switch ? '<i data-lucide="play-circle"></i> 긴급 정지 풀기' : '<i data-lucide="octagon-x"></i> 긴급 정지';
  $("#kill-btn").classList.toggle("on", s.kill_switch);
  const ks = $("#kill-strip");
  if (ks) {
    ks.classList.toggle("hidden", !s.kill_switch);
    if (s.kill_switch) {
      const since = s.kill_since ? new Date(s.kill_since).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
      ks.innerHTML = `<i data-lucide="octagon-x"></i><div><b>긴급 정지 중 — 새로 사지 않아요</b><span>${since ? since + "부터 · " : ""}${esc(s.kill_reason || "")} · 가진 종목의 손절·익절 매도는 계속돼요. 직접 풀기 전까지 그대로예요.</span></div>
        <button id="kill-strip-off">긴급 정지 풀기</button>`;
      $("#kill-strip-off").onclick = () => $("#kill-btn").click();
    }
  }
  const ac = $("#auto-card");
  if (ac) {
    ac.classList.toggle("on", s.auto_trading);
    ac.classList.toggle("killed", s.kill_switch);
    $("#auto-title").textContent = s.kill_switch ? "긴급 정지 중 · 새로 사지 않음 (손절·익절은 계속)" : s.auto_trading ? "자동매매 실행 중" : "자동매매 꺼짐";
    const next = Object.entries(s.markets).filter(([, x]) => x.enabled).map(([m, x]) =>
      `${MARKET_NAME[m]} ${STRAT_NAME[x.strategy] || x.strategy} · 다음 ${x.next_run.replace("T", " ").slice(5, 16)}`).join(" / ");
    $("#auto-sub").textContent = `${MODE_NAME[s.mode]} · ${next || "사용 중인 시장 없음"}`;
  }
  icons();
  const msgs = [s.error, s.scheduler_error && "스케줄러: " + s.scheduler_error].filter(Boolean);
  if (msgs.length) banner(msgs.join(" / "));
  else if (!s.keys.toss && ["mkt", "stock"].includes(state.tab)) banner("토스증권 키가 없어 체험용 데이터를 보여줍니다. 설정에서 토스증권 Client ID/Secret 을 등록하면 실제 시세로 바뀝니다.", "info");
  else if (!s.keys.anthropic && state.tab === "office") banner("AI 회의와 큐비를 쓰려면 설정에서 Claude API 키를 등록하세요.", "info");
  else banner("");
}

function renderPositions(rows) {
  $("#pos-table tbody").innerHTML = rows.length ? rows.map((p) => {
    const pl = state.plans[p.symbol];
    return `<tr data-open="${esc(p.symbol)}" class="clickable">
    <td>${MARKET_NAME[p.market]}</td><td>${logo(p.symbol, p.symbol, 20)} ${esc(p.symbol)}</td><td class="r">${p.qty.toLocaleString()}</td>
    <td class="r">${money(p.avg_price, p.market)}</td><td class="r">${money(p.last_price, p.market)}</td>
    <td class="r ${cls(p.pnl)}">${pct(p.pnl)}</td>
    <td class="r down">${pl ? money(pl.stop, p.market) : "-"}</td><td class="r up">${pl ? money(pl.take, p.market) : "-"}</td></tr>`;
  }).join("") : `<tr><td colspan="8" class="note">보유 종목이 없습니다.</td></tr>`;
}

function reasonHtml(text) {
  return String(text || "").split(" | ").map((x) => `<li>${esc(x)}</li>`).join("");
}

function renderOrders(rows) {
  const st = { submitted: "접수", filled: "체결", rejected: "거부", dry_run: "계산만" };
  state.orders = rows;
  const stc = { submitted: "amber", filled: "green", rejected: "red", dry_run: "" };
  $("#ord-table tbody").innerHTML = rows.length ? rows.map((o, i) => `<tr class="clickable" data-ord="${i}">
    <td>${esc(o.trade_date.slice(5))}</td><td><div class="namecell">${logo(o.symbol, o.name || o.symbol, 22)}<div><div class="nm">${esc(o.name || o.symbol)}</div><div class="sym">${esc(o.symbol)}</div></div></div></td>
    <td><span class="side ${o.side}">${o.side === "buy" ? "매수" : "매도"}</span>${o.strategy === "ai-exit" ? ' <span class="badge">자동 손익절</span>' : ""}</td>
    <td class="r">${o.qty.toLocaleString()}</td><td class="r">${money(o.price, o.market)}</td><td class="r">${money(o.price * o.qty, o.market)}</td>
    <td><span class="badge ${stc[o.status] || ""}">${st[o.status] || o.status}</span></td>
    <td class="note reason-cell">${esc(String(o.message || "").split(" | ")[0])}</td></tr>`).join("")
    : `<tr><td colspan="8" class="note">주문이 없습니다.</td></tr>`;
}
$("#ord-table").addEventListener("click", (e) => {
  const tr = e.target.closest("[data-ord]");
  if (!tr) return;
  const o = state.orders[tr.dataset.ord];
  modal(`<h3>${o.side === "buy" ? "매수" : "매도"} ${esc(o.name || o.symbol)} ${o.qty}주 · ${money(o.price, o.market)}</h3>
    <p class="note">${esc(o.ts)} · ${esc(o.strategy || "")} · ${esc(o.status)}</p><h4>이 주문의 근거</h4><ul class="why">${reasonHtml(o.message)}</ul>`);
});

function renderApprovals(rows) {
  $("#approvals-panel")?.classList.toggle("hidden", !rows.length);
  $("#approvals-panel").classList.toggle("hidden", !rows.length);
  $("#appr-list").innerHTML = rows.map((a) => `<div class="appr ${a.side}">
    <div class="appr-head">${logo(a.symbol, a.symbol, 32)}<div><b>${a.side === "buy" ? "매수" : "매도"} ${esc(a.symbol)} ${a.qty.toLocaleString()}주</b>
      <div class="note">${esc(a.trade_date)} · 주문가 ${money(a.price, a.market)} · 약 ${money(a.price * a.qty, a.market)}</div></div>
      <div class="appr-btns"><button class="primary" data-appr="${a.id}" data-ok="1">근거 확인했고 승인</button><button data-appr="${a.id}" data-ok="0">거절</button></div></div>
    <ul class="why">${reasonHtml(a.reason)}</ul></div>`).join("");
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-appr]");
  if (!b) return;
  const ok = b.dataset.ok === "1";
  if (ok && LIVE.has(state.status?.mode) && (await ask("실제 돈으로 주문합니다. 진행하려면 아래 칸에 '실전주문' 이라고 입력하세요.", { input: "확인 문구", danger: true, ok: "실전 주문" })) !== "실전주문") return;
  try {
    const r = await api(`/api/approvals/${b.dataset.appr}`, { method: "POST", body: { approve: ok } });
    toast(ok ? `주문 결과: ${r.status} ${r.message || ""}` : "거절했습니다.");
  } catch (err) { toast(err.message); }
  refresh();
});

function renderEvents(rows) {
  $("#events").innerHTML = rows.map((e) => `<li class="${e.level}"><span class="t">${esc(new Date(e.ts).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }))}</span>${esc(e.message)}</li>`).join("")
    || `<li class="note">로그가 없습니다.</li>`;
}

async function runNow(dry) {
  const m = $("#run-market").value;
  const s = state.status;
  if (!dry) {
    if (!(await ask(`${MODE_NAME[s.mode]} 계좌로 ${MARKET_NAME[m]} 주문을 지금 냅니다. 계속할까요?`))) return;
    if (LIVE.has(s.mode) && (await ask("실제 돈으로 주문합니다. 진행하려면 아래 칸에 '실전주문' 이라고 입력하세요.", { input: "확인 문구", danger: true, ok: "실전 주문" })) !== "실전주문") return;
  }
  $("#run-out").textContent = "실행 중...";
  try {
    const r = await api("/api/run-once", { method: "POST", body: { market: m, dry_run: dry } });
    $("#run-out").textContent = formatRun(r);
    if (window.acctTab) window.acctTab("run");
  } catch (e) { $("#run-out").textContent = "실패: " + e.message; }
  refresh();
}

function formatRun(r) {
  if (r.error || r.skipped) return r.error || r.skipped;
  const lines = [`${r.trade_date} · ${STRAT_NAME[r.strategy] || r.strategy} · 운용금액 ${Math.round(r.capital).toLocaleString()}${r.dry_run ? " · 계산만" : ""}`];
  lines.push("목표 비중: " + (Object.entries(r.target).map(([k, v]) => `${k} ${(v * 100).toFixed(1)}%`).join(", ") || "전부 현금"));
  if (r.ai && r.ai.length) { lines.push("", "AI 회의 반영"); r.ai.forEach((a) => lines.push(`  ${a.symbol}: ${a.action}`)); }
  lines.push("", `주문 ${r.orders.length}건`);
  r.orders.forEach((o) => lines.push(`  ${o.side === "buy" ? "매수" : "매도"} ${o.symbol} ${o.qty}주 @ ${o.price} | ${o.status} ${o.message || ""}`));
  if (r.blocked.length) { lines.push("", `차단 ${r.blocked.length}건`); r.blocked.forEach((b) => lines.push(`  ${b.symbol}: ${b.reason}`)); }
  if (r.data_errors.length) { lines.push("", "데이터 오류"); r.data_errors.forEach((e) => lines.push("  " + e)); }
  if (r.kill_switch) lines.push("", "킬 스위치가 켜져 있어 신규 매수를 하지 않았습니다.");
  return lines.join("\n");
}

$("#dry-btn").onclick = () => runNow(true);
$("#run-btn").onclick = async () => {
  if (!(await ask("실행 시각을 기다리지 않고 지금 바로 주문까지 실행합니다. 진행할까요?"))) return;
  $("#run-btn").closest("details").open = false;
  runNow(false);
};
$("#auto-btn").onclick = async () => {
  const s = state.status;
  const on = !s.auto_trading;
  if (on) {
    const c = state.config || await api("/api/config");
    // AI 전략은 켜 둔 세션 내내 수시로 회의하고 사고판다. 단순 규칙 전략은 정기 실행 시각에 하루 한 번
    const sessions = (m, mc) => m === "KR" ? (mc.extended ? "넥스트레이드 08:00~20:00" : "정규장 09:00~15:30")
      : ["주간거래", "프리마켓", "정규장", "애프터마켓"].filter((n, i) => i === 2 || (mc.extended_sessions || []).includes(["day", "pre", "", "after"][i])).join("·");
    const lines = Object.entries(c.markets).filter(([, mc]) => mc.enabled).map(([m, mc]) =>
      `· ${MARKET_NAME[m]}: ${STRAT_NAME[mc.strategy]} 전략, ` + (mc.strategy === "ai" && c.ai.intraday !== false
        ? `${sessions(m, mc)} 동안 수시로 회의하고 사고팔아요` : `매 거래일 ${mc.run_time}(${m === "KR" ? "한국" : "뉴욕"} 시간)`)
      + `, 한도 ${m === "US" ? usdWithKrw(mc.budget) : money(mc.budget, m)}`);
    const rules = c.markets.KR.strategy === "ai" || c.markets.US.strategy === "ai"
      ? "\n· AI 회의 결과가 매수여도 체크리스트(추세·모멘텀·과열·변동성·손익비·팩트체크)를 모두 넘어야 삽니다.\n· 산 종목은 손절가·익절가에 닿으면 자동으로 팝니다." + (c.ai.require_approval ? "\n· 주문은 승인 대기에 먼저 올라오고, 근거를 보고 승인해야 나갑니다." : "")
      : "";
    if (!(await ask(`${MODE_NAME[s.mode]} 계좌로 자동매매를 시작합니다.\n\n${lines.join("\n")}${rules}\n\n종목당 최대 ${Math.round(c.risk.max_weight * 100)}%, 하루 손실 ${Math.round(c.risk.daily_loss_limit * 100)}% 에 닿으면 긴급 정지(새로 사지 않음)가 켜집니다. 계속할까요?`))) return;
    if (LIVE.has(s.mode) && (await ask("실전 자동매매입니다. 진행하려면 아래 칸에 '실전주문' 이라고 입력하세요.", { input: "확인 문구", danger: true, ok: "실전 자동매매 시작" })) !== "실전주문") return;
  }
  await api("/api/auto", { method: "POST", body: { on } }).catch((e) => toast(e.message));
  refresh();
};
$("#kill-help").onclick = () => modal(`<h3><i data-lucide="octagon-x"></i> 긴급 정지 (킬 스위치)</h3>
  <p>자동매매의 <b>비상 브레이크</b>입니다. 켜면 그 순간부터 <b>새로 사는 주문만</b> 모두 멈춥니다.</p>
  <ul><li>이미 가진 종목의 <b>손절·익절 매도는 계속</b>됩니다. 손실을 키우지 않기 위해서예요.</li>
  <li>하루 손실 한도(설정의 '하루 손실 한도')를 넘으면 <b>자동으로 켜집니다</b>. 장중에도 2분마다 확인합니다.</li>
  <li>시장이 크게 흔들리거나, 이상한 주문이 보이거나, 잠시 쉬고 싶을 때 직접 눌러도 됩니다.</li>
  <li>다시 사게 하려면 '긴급 정지 풀기'를 누르세요. 자동으로 풀리지 않습니다.</li></ul>
  <p class="note">자동매매 켜기·끄기와는 별개입니다. 자동매매를 끄면 손절·익절 감시도 멈추고, 긴급 정지는 사는 것만 멈춥니다.</p>`) || icons();
$("#kill-btn").onclick = async () => {
  if (!state.status.kill_switch && !(await ask("긴급 정지를 켭니다. 새로 사는 주문만 멈추고, 손절·익절 매도는 계속됩니다."))) return;
  await api("/api/kill-switch", { method: "POST", body: { on: !state.status.kill_switch } }).catch((e) => toast(e.message));
  refresh();
};
document.addEventListener("tab", (e) => refresh());

function readParams(root) {
  const out = {};
  root.querySelectorAll("[data-param]").forEach((i) => { if (i.value !== "") out[i.dataset.param] = Number(i.value); });
  return out;
}

// 설정
// 퍼센트로 보여 주는 칸 (저장은 0~1 비율)
const PCT_IDS = ["r-maxw", "r-maxe", "r-loss", "sec-max", "rl-conf", "rl-vol", "rl-fact"];
const setPct = (id, v) => { $("#" + id).value = v == null ? "" : +(v * 100).toFixed(2); };
const getPct = (id) => Number($("#" + id).value) / 100;
const MODELS = [["claude-sonnet-5-5", "Sonnet 5.5 · 빠르고 균형 (추천: 빠른 모델)"], ["claude-opus-5-5", "Opus 5.5 · 깊게 생각 (추천: 깊은 모델)"],
  ["claude-haiku-5-5", "Haiku 5.5 · 가장 저렴"], ["claude-fable-5-1", "Fable 5.1 · 최고 성능, 가장 비쌈"]];
const modelSelect = (id, cur) => {
  const list = MODELS.some(([k]) => k === cur) || !cur ? MODELS : [...MODELS, [cur, cur + " (직접 입력한 모델)"]];
  $("#" + id).innerHTML = list.map(([k, n]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${n}</option>`).join("");
};

async function loadConfig() {
  const c = await api("/api/config");
  const pr = await api("/api/principles");
  state.config = c;
  renderModeNow();
  $("#data-source").value = c.data_source || "auto";
  $("#kis-account").value = c.kis.account || "";
  $("#toss-loc").checked = !!c.toss.us_loc;
  $("#toss-guard").checked = c.toss.server_guard !== false;
  $("#sec-contact").value = c.sec_contact || "";
  setPct("r-maxw", c.risk.max_weight); setPct("r-maxe", c.risk.max_exposure); setPct("r-loss", c.risk.daily_loss_limit);
  $("#r-ordkr").value = c.risk.max_order_value.KR; $("#r-ordus").value = c.risk.max_order_value.US;
  $("#news-feeds").value = (c.news?.feeds || []).map((f) => `${f.name}|${f.url}|${f.cat || "kr"}`).join("\n");
  $("#tg-chat").value = c.notify?.telegram_chat_id || "";
  $("#ntf-report").checked = c.notify?.daily_report !== false;
  $("#ai-cap").value = c.ai.monthly_cost_cap_krw ?? 30000;
  setPct("sec-max", c.max_sector_weight ?? 0.4);
  $("#ai-mode").value = c.ai.mode || "fast";
  modelSelect("ai-quick", c.ai.quick_model); modelSelect("ai-deep", c.ai.deep_model || c.ai.model);
  $("#ai-effort").value = c.ai.effort; $("#ai-chat-effort").value = c.ai.chat_effort || "medium";
  $("#ai-profile").value = c.ai.profile || "";
  $("#ai-approve").checked = !!c.ai.require_approval;
  $("#ai-exit").checked = c.ai.auto_exit !== false;
  $("#ai-debate").value = c.ai.debate_rounds;
  $("#ai-before").value = c.ai.run_before_minutes;
  $("#ai-web").checked = !!c.ai.web_research;
  $("#ai-autopick").checked = c.ai.auto_pick !== false;
  const R = pr.rules;
  setPct("rl-conf", R.min_confidence); $("#rl-rr").value = R.min_reward_risk; $("#rl-rsi").value = R.max_rsi;
  setPct("rl-vol", R.max_vol); setPct("rl-fact", R.min_fact_ok); $("#rl-buys").value = R.max_new_buys_per_day;
  $("#rl-cool").value = R.cooldown_days; $("#rl-trend").checked = R.require_trend; $("#rl-mom").checked = R.require_momentum;
  $("#rl-market").value = R.market_filter || "half";
  $$("input[name=holding]").forEach((i) => (i.checked = i.value === (c.ai.holding || "auto")));
  const ruleKeys = Object.keys(state.strategies);
  const stratSelect = (cur) => `<option value="ai" ${cur === "ai" ? "selected" : ""}>${STRAT_NAME.ai} (추천)</option>
    <optgroup label="단순 규칙 (AI 회의 없이, 고급 사용자용)">${ruleKeys.map((k) => `<option value="${k}" ${k === cur ? "selected" : ""}>${STRAT_NAME[k] || k}</option>`).join("")}</optgroup>`;
  $("#market-cfg").innerHTML = Object.entries(c.markets).map(([m, mc]) => `
    <div class="mkt-cfg ${mc.enabled ? "on" : ""}" data-market="${m}">
      <label class="mkt-head"><span class="flag">${m === "KR" ? "🇰🇷" : "🇺🇸"}</span><b>${MARKET_NAME[m]} 주식</b>
        <span class="sw"><input type="checkbox" data-f="enabled" ${mc.enabled ? "checked" : ""}><i></i></span><em class="sw-txt">${mc.enabled ? "자동매매 켬" : "자동매매 끔"}</em></label>
      <label>이 시장에 쓸 돈 <em class="hint">${m === "KR" ? "원" : "달러"} · 계좌 돈 중 이만큼만 자동매매에 써요</em><input data-f="budget" type="number" value="${mc.budget}"></label>
      <div class="mkt-ai"><label class="check"><input type="checkbox" data-f="ai_meetings" ${mc.ai_meetings !== false ? "checked" : ""}> ${MARKET_NAME[m]} AI 회의</label>
        <span class="mkt-ai-n">하루 <input data-f="meetings_per_day" type="number" min="0" max="50" value="${mc.meetings_per_day ?? 4}"> 종목까지</span>
        <em class="hint">회의 1번에 Claude 7번 + 웹 검색이라, 많을수록 비용이 늘어요</em></div>
      <details class="mini-adv"><summary>시간 · 종목 · 고르는 방법 (고급)</summary>
        <label>무엇을 살지 고르는 방법 <select data-f="strategy">${stratSelect(mc.strategy)}</select></label>
        ${m === "KR" ? `<div class="form-row"><label>정기 실행 시각 <em class="hint">한국 시각</em><input data-f="run_time" value="${esc(mc.run_time)}"></label></div>
          <label class="check"><input type="checkbox" data-f="extended" ${mc.extended ? "checked" : ""}> 시간외(넥스트레이드 08:00~20:00)에도 감시·주문 <em class="hint">지정가로만 나가요</em></label>`
        : `<div class="form-row"><label>정기 실행 <select data-f="run_session"><option value="regular" ${mc.run_session !== "day" ? "selected" : ""}>뉴욕 정규장 마감 전 (한국 밤)</option><option value="day" ${mc.run_session === "day" ? "selected" : ""}>한국 낮 주간거래</option></select></label>
            <label>실행 시각 <em class="hint">정규장=뉴욕, 주간거래=한국 시각</em><input data-f="run_time" value="${esc(mc.run_time)}"></label>
            <label>주간거래 시간 <em class="hint">한국 시각</em><input data-f="day_hours" value="${esc(mc.day_hours || "10:00-17:30")}"></label></div>
          <div class="form-row">${[["day", "주간거래 (한국 낮)"], ["pre", "프리마켓"], ["after", "애프터마켓"]].map(([k, n]) =>
            `<label class="check"><input type="checkbox" data-ext="${k}" ${(mc.extended_sessions || []).includes(k) ? "checked" : ""}> ${n}에도 감시·주문</label>`).join("")}</div>
          <p class="note">미국 주식은 달러로만 사요. 달러가 모자라면 자동 환전 대신 환전 우대로 바꾸라고 알려 드려요.</p>`}
        <label>늘 살펴볼 종목 <em class="hint">쉼표로 구분${m === "US" ? ". 나스닥 외에는 NYSE:KO 처럼" : ""}. AI가 매일 고르는 후보에 더해져요</em><input data-f="universe" value="${esc(mc.universe.join(", "))}"></label>
        <div class="form-row params" data-params></div>
      </details>
    </div>`).join("");
  $$("#market-cfg [data-market]").forEach((el) => {
    const m = el.dataset.market;
    const draw = () => {
      const name = el.querySelector("[data-f=strategy]").value;
      const vals = name === c.markets[m].strategy ? c.markets[m].params : {};
      el.querySelector("[data-params]").innerHTML = name === "ai"
        ? `<p class="note">정기 실행 ${c.ai.run_before_minutes}분 전부터 후보마다 AI 투자 회의를 열고, 결론과 '사기 전 확인 규칙'을 통과한 종목만 삽니다.</p>`
        : Object.entries(state.strategies[name] || {}).map(([k, v]) =>
          `<label>${PARAM_NAME[k] || k}<input data-param="${k}" type="number" step="any" value="${vals[k] ?? v}"></label>`).join("");
    };
    el.querySelector("[data-f=strategy]").onchange = draw;
    el.querySelector("[data-f=enabled]").onchange = (e) => {
      el.classList.toggle("on", e.target.checked);
      el.querySelector(".sw-txt").textContent = e.target.checked ? "자동매매 켬" : "자동매매 끔";
    };
    draw();
  });
  refreshKeyState();
  setDirty(false);
  icons();
}

// 저장하지 않은 변경 표시 (키·업데이트 칸은 따로 저장되니 제외)
let cfgDirty = false;
function setDirty(v) { cfgDirty = v; $("#cfg-dirty")?.classList.toggle("hidden", !v); }
$("#tab-cfg").addEventListener("input", (e) => { if (!e.target.closest("#cfg-keys, #upd-panel")) setDirty(true); });
$("#tab-cfg").addEventListener("change", (e) => { if (!e.target.closest("#cfg-keys, #upd-panel")) setDirty(true); });
document.addEventListener("tab", (e) => { if (e.detail !== "cfg" && cfgDirty) toast("설정에 저장하지 않은 변경이 있어요. 설정 탭 아래 '설정 저장'을 눌러 주세요", "circle-dot"); });

// 운용 모드: 지금 상태를 크게. 실전으로 갈 때만 따로 확인 (설정 저장 버튼과 별개로 바로 적용)
// '가상매매로' / '토스증권 실전으로' (받침이 있으면 '으로', ㄹ 받침은 '로')
const ro = (w) => { const c = w.charCodeAt(w.length - 1) - 0xac00; const j = c >= 0 && c < 11172 ? c % 28 : 0; return w + (j && j !== 8 ? "으로" : "로"); };
function renderModeNow() {
  const m = state.config?.mode || "paper", live = LIVE.has(m);
  const el = $("#mode-now");
  if (!el) return;
  el.className = "mode-now " + (live ? "live" : "paper");
  el.innerHTML = live
    ? `<div class="mn-ico"><i data-lucide="flame"></i></div><div class="mn-txt"><b>지금은 실전매매예요</b><span>${MODE_NAME[m]} · 주문이 <u>실제 돈</u>으로 나가요. 모든 화면 맨 위에 빨간 띠가 보여요</span></div>
       <button data-mode-go="paper"><i data-lucide="flask-conical"></i> 가상매매로 돌아가기</button>`
    : `<div class="mn-ico"><i data-lucide="flask-conical"></i></div><div class="mn-txt"><b>지금은 ${m === "kis_demo" ? "모의투자" : "가상매매"}예요</b><span>${MODE_NAME[m]} · 실제 시세로 연습만 해요. <u>실제 돈은 쓰지 않아요</u></span></div>
       <button class="danger" data-mode-go="toss_real"><i data-lucide="flame"></i> 토스증권 실전으로 바꾸기</button>`;
  icons();
}
async function applyMode(target) {
  const live = LIVE.has(target);
  const cur = await api("/api/config");
  try { state.config = await api("/api/config", { method: "PUT", body: { ...cur, mode: target, live_confirmed: live } }); }
  catch (e) { toast(e.message); return; }
  toast(live ? `${ro(MODE_NAME[target])} 바꿨어요. 자동매매는 꺼진 상태예요` : `${ro(MODE_NAME[target])} 바꿨어요. 이제 실제 돈은 쓰지 않아요`, live ? "flame" : "flask-conical");
  renderModeNow();
  refresh();
}
async function switchMode(target) {
  if (!LIVE.has(target)) {
    if (await ask(`${ro(MODE_NAME[target])} 바꿀까요?\n자동매매는 꺼진 상태로 시작해요. 실전 계좌의 주식·주문은 그대로 남아요.`)) applyMode(target);
    return;
  }
  const keyKind = target === "toss_real" ? "toss" : target;
  const hasKey = !!state.status?.keys?.[keyKind];
  modal(`<h3><i data-lucide="flame"></i> ${ro(MODE_NAME[target])} 바꾸기</h3>
    ${hasKey ? "" : `<p class="bad-note">먼저 '2. 연결'에 ${keyKind === "toss" ? "토스증권" : "한국투자증권"} 키를 저장하고 '연결 테스트'를 해 주세요.</p>`}
    <ul class="live-list"><li>앞으로 주문이 <b>실제 돈</b>으로 나가요. 손실도 실제예요.</li>
      <li>바꾸면 자동매매는 <b>꺼진 상태</b>가 돼요. 켤 때 한 번 더 확인해요.</li>
      <li>실전인 동안 모든 화면 맨 위에 <b class="down">빨간 '실전' 띠</b>가 보이고, 주문 버튼에도 '실전'이 붙어요.</li>
      <li>언제든 '가상매매로 돌아가기' 한 번이면 돌아와요.</li></ul>
    <label class="check"><input type="checkbox" id="lv-ok"> 손실 책임이 저에게 있음을 이해했습니다</label>
    <label>확인을 위해 <b>실전</b> 이라고 입력하세요 <input id="lv-word" autocomplete="off"></label>
    <div class="form-row"><button id="lv-cancel">취소</button><button class="danger" id="lv-go" disabled>실전으로 바꾸기</button></div>`);
  icons();
  const ok = () => { $("#lv-go").disabled = !(hasKey && $("#lv-ok").checked && $("#lv-word").value.trim() === "실전"); };
  $("#lv-ok").onchange = ok; $("#lv-word").oninput = ok;
  $("#lv-cancel").onclick = () => $("#modal").classList.add("hidden");
  $("#lv-go").onclick = () => { $("#modal").classList.add("hidden"); applyMode(target); };
}
document.addEventListener("click", (e) => { const b = e.target.closest("[data-mode-go]"); if (b) switchMode(b.dataset.modeGo); });

// 키 종류마다 칸 이름·예시·받는 곳이 바뀐다
const KEY_LABELS = {
  toss: ["Client ID", "Client Secret", "", "토스증권 WTS → 설정 → Open API 에서 발급. 허용 IP 에 이 PC 공인 IP 를 등록해야 주문돼요."],
  anthropic: ["API 키", null, "sk-ant-로 시작하는 키", "console.anthropic.com → API Keys → Create Key. 처음 한 번만 보이니 바로 복사하세요."],
  dart: ["인증키 (40자리)", null, "영문·숫자 40자리", "opendart.fss.or.kr → 인증키 신청/관리 → 인증키 신청 (무료)."],
  telegram: ["봇 토큰", null, "123456789:AAH... 처럼 생긴 값", "텔레그램 @BotFather 에게 /newbot. 저장한 뒤 내 봇에게 아무 말이나 보내고, '5. 알림·이 PC'에서 '대화방 번호 자동으로 찾기'를 누르세요."],
  kis_demo: ["앱키", "앱시크릿", "", "KIS Developers 에서 모의투자 앱키 발급."],
  kis_real: ["앱키", "앱시크릿", "", "KIS Developers 에서 실전 앱키 발급."],
};
function refreshKeyState() {
  const kind = $("#key-kind").value;
  const [a, b, ph, how] = KEY_LABELS[kind];
  $("#key-label1").textContent = a;
  $("#key-app").placeholder = ph || "";
  $("#key-app").type = kind === "toss" || kind.startsWith("kis") ? "text" : "password";
  $("#key-secret-wrap").classList.toggle("hidden", !b);
  if (b) $("#key-label2").textContent = b;
  $("#key-how").textContent = how;
  const s = state.status;
  if (s) {
    $("#key-state").innerHTML = s.keys[kind] ? "<b class='ok'>저장된 키 있음</b>" : "저장된 키 없음";
    const names = { toss: "토스증권", anthropic: "Claude", dart: "DART", telegram: "텔레그램", kis_demo: "KIS 모의", kis_real: "KIS 실전" };
    $("#key-status").innerHTML = Object.entries(names).map(([k, n]) =>
      `<button type="button" class="kchip ${s.keys[k] ? "on" : ""} ${k === kind ? "sel" : ""}" data-kind="${k}"><i data-lucide="${s.keys[k] ? "check-circle-2" : "circle-dashed"}"></i>${n}</button>`).join("");
    window.lucide && lucide.createIcons();
  }
}
$("#key-kind").onchange = () => { $("#key-app").value = ""; $("#key-secret").value = ""; refreshKeyState(); };
// 위의 키 칩을 누르면 그 종류로
$("#key-status").addEventListener("click", (e) => { const b = e.target.closest("[data-kind]"); if (b) { $("#key-kind").value = b.dataset.kind; $("#key-kind").onchange(); } });

$("#cfg-save").onclick = async () => {
  // 서버의 최신 설정 위에 화면 칸을 덮어쓴다 (운용 모드는 위 '운용 모드'에서만 바뀐다)
  const c = await api("/api/config");
  c.data_source = $("#data-source").value;
  c.kis.account = $("#kis-account").value.trim();
  c.toss.us_loc = $("#toss-loc").checked;
  c.toss.server_guard = $("#toss-guard").checked;
  c.sec_contact = $("#sec-contact").value.trim();
  c.risk.max_weight = getPct("r-maxw"); c.risk.max_exposure = getPct("r-maxe");
  c.risk.daily_loss_limit = getPct("r-loss");
  c.risk.max_order_value = { KR: Number($("#r-ordkr").value), US: Number($("#r-ordus").value) };
  c.news = { feeds: $("#news-feeds").value.split("\n").map((l) => l.split("|")).filter((p) => p.length >= 2 && /^https?:\/\//.test(p[1].trim()))
    .map(([n, u, k]) => ({ name: n.trim(), url: u.trim(), cat: (k || "").trim() === "global" ? "global" : "kr" })) };
  c.notify = { ...(c.notify || {}), telegram_chat_id: $("#tg-chat").value.trim(), daily_report: $("#ntf-report").checked };
  c.max_sector_weight = getPct("sec-max") || 0.4;
  const conf = getPct("rl-conf");
  Object.assign(c.ai, {
    mode: $("#ai-mode").value, quick_model: $("#ai-quick").value, deep_model: $("#ai-deep").value,
    effort: $("#ai-effort").value, chat_effort: $("#ai-chat-effort").value, profile: $("#ai-profile").value.trim(),
    require_approval: $("#ai-approve").checked, auto_exit: $("#ai-exit").checked, debate_rounds: Number($("#ai-debate").value),
    run_before_minutes: Number($("#ai-before").value),
    min_confidence: conf, web_research: $("#ai-web").checked,  // 확신도 기준은 하나로 (회의 비중·체크리스트 같이)
    monthly_cost_cap_krw: Number($("#ai-cap").value) || 0, auto_pick: $("#ai-autopick").checked,
    holding: ($$("input[name=holding]").find((i) => i.checked) || {}).value || "auto",
    rules: { min_confidence: conf, min_reward_risk: Number($("#rl-rr").value),
      max_rsi: Number($("#rl-rsi").value), max_vol: getPct("rl-vol"), min_fact_ok: getPct("rl-fact"),
      max_new_buys_per_day: Number($("#rl-buys").value), cooldown_days: Number($("#rl-cool").value),
      require_trend: $("#rl-trend").checked, require_momentum: $("#rl-mom").checked, market_filter: $("#rl-market").value },
  });
  $$("#market-cfg [data-market]").forEach((el) => {
    const mc = c.markets[el.dataset.market];
    mc.enabled = el.querySelector("[data-f=enabled]").checked;
    mc.strategy = el.querySelector("[data-f=strategy]").value;
    mc.run_time = el.querySelector("[data-f=run_time]").value.trim();
    if (el.dataset.market === "KR") mc.extended = el.querySelector("[data-f=extended]").checked;
    else {
      mc.run_session = el.querySelector("[data-f=run_session]").value;
      mc.day_hours = el.querySelector("[data-f=day_hours]").value.trim() || "10:00-17:30";
      mc.extended_sessions = [...el.querySelectorAll("[data-ext]")].filter((x) => x.checked).map((x) => x.dataset.ext);
    }
    mc.budget = Number(el.querySelector("[data-f=budget]").value);
    mc.ai_meetings = el.querySelector("[data-f=ai_meetings]").checked;
    mc.meetings_per_day = Math.max(0, Math.round(Number(el.querySelector("[data-f=meetings_per_day]").value) || 0));
    mc.universe = el.querySelector("[data-f=universe]").value.split(",").map((x) => x.trim()).filter(Boolean);
    if (mc.strategy !== "ai") mc.params = readParams(el.querySelector("[data-params]"));
  });
  try {
    state.config = await api("/api/config", { method: "PUT", body: c });
    $("#cfg-msg").textContent = "저장했습니다.";
    setDirty(false);
    refresh();
  } catch (e) { $("#cfg-msg").textContent = "저장 실패: " + e.message; }
};

$("#key-save").onclick = async () => {
  try {
    const r = await api("/api/secrets", { method: "PUT", body: { kind: $("#key-kind").value, key: $("#key-app").value, secret: $("#key-secret").value } });
    $("#key-app").value = ""; $("#key-secret").value = "";
    await refresh(); refreshKeyState();
    if (!r.keys[$("#key-kind").value]) return toast("저장이 확인되지 않았습니다. 다시 시도하세요.");
    toast("키를 저장했어요. 연결을 시험해 볼게요", "save");
    keyTest();
  } catch (e) { toast("저장 실패: " + e.message + (e.message.includes("토큰") ? "\n페이지를 새로고침(F5)한 뒤 다시 저장하세요." : "")); }
};
// 저장한 키가 실제로 되는지 단계별로 시험 (주문은 내지 않는다)
async function keyTest() {
  const kind = $("#key-kind").value, out = $("#key-test-out");
  if (!["toss", "anthropic", "dart", "telegram"].includes(kind)) { out.classList.add("hidden"); return toast("이 키는 연결 테스트를 지원하지 않아요"); }
  out.classList.remove("hidden");
  out.innerHTML = `<div class="kt-row run"><span class="kt-ico"><i class="spin"></i></span><b>연결을 시험하는 중...</b></div>`;
  try {
    const r = await api("/api/secrets/test", { method: "POST", body: { kind } });
    out.innerHTML = `<div class="kt-head ${r.ok ? "ok" : "bad"}">${r.ok ? "연결 성공 — 이 키로 바로 쓸 수 있어요" : "연결이 안 되는 단계가 있어요"}</div>` +
      r.steps.map((s) => `<div class="kt-row ${s.ok ? "ok" : "bad"}"><span class="kt-ico">${s.ok ? "✓" : "!"}</span><div><b>${esc(s.name)}</b><span>${esc(s.detail)}</span></div></div>`).join("");
    animateIn("#key-test-out .kt-row", { step: 80 });
  } catch (e) { out.innerHTML = `<div class="kt-head bad">${esc(e.message)}</div>`; }
}
$("#key-test").onclick = keyTest;

$("#key-del").onclick = async () => {
  if (!(await ask("이 키를 삭제할까요?"))) return;
  await api("/api/secrets/" + $("#key-kind").value, { method: "DELETE" }).catch((e) => toast(e.message));
  await refresh(); refreshKeyState();
};

// 시작
(async () => {
  setTimeout(moveInk, 200); document.fonts?.ready.then(moveInk);
  state.strategies = await api("/api/strategies");
  await refresh();
  setInterval(refresh, 15000);
})();

// 머리말 높이(창이 좁으면 두 줄)에 맞춰 설정 바로가기 줄이 머리말을 가리지 않게
const setHdrH = () => document.documentElement.style.setProperty("--hdr-h", ($("header.top")?.offsetHeight || 56) + "px");
window.addEventListener("resize", setHdrH);
setTimeout(setHdrH, 300);
