"use strict";
// 떠 있는 AI 애널리스트 챗봇 '큐비'. 화면 어디로든 끌어서 옮길 수 있고, 위치는 이 PC 브라우저에 기억한다.
// 답변은 서버의 비서(Assistant)가 시세·계좌·최근 AI 회의 결론을 도구로 확인해서 만든다.

const QBI_TPL = `
<svg viewBox="0 0 120 130" class="qbi-svg" aria-hidden="true">
  <defs>
    <radialGradient id="qb-body" cx="38%" cy="30%" r="75%">
      <stop offset="0" stop-color="#e9f6ff"/><stop offset=".45" stop-color="#9fd4ff"/><stop offset="1" stop-color="#3f7fe0"/>
    </radialGradient>
    <radialGradient id="qb-face" cx="45%" cy="35%" r="70%">
      <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#dfeaf7"/>
    </radialGradient>
    <radialGradient id="qb-coin" cx="35%" cy="30%" r="70%">
      <stop offset="0" stop-color="#fff3b0"/><stop offset=".6" stop-color="#f5c542"/><stop offset="1" stop-color="#b8860b"/>
    </radialGradient>
    <linearGradient id="qb-belly" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#14223a"/><stop offset="1" stop-color="#0b1424"/>
    </linearGradient>
    <filter id="qb-soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="2.2"/></filter>
  </defs>
  <ellipse cx="60" cy="124" rx="30" ry="4.5" fill="rgba(0,0,0,.28)" class="qbi-shadow"/>
  <g class="qbi-float">
    <line x1="60" y1="16" x2="60" y2="28" stroke="#3f7fe0" stroke-width="3" stroke-linecap="round"/>
    <g class="qbi-coin"><circle cx="60" cy="12" r="8" fill="url(#qb-coin)"/><text x="60" y="15.5" text-anchor="middle" font-size="9" font-weight="900" fill="#8a5a00">Q</text></g>
    <ellipse cx="22" cy="56" rx="9" ry="13" fill="url(#qb-body)"/><ellipse cx="98" cy="56" rx="9" ry="13" fill="url(#qb-body)"/>
    <rect x="18" y="26" width="84" height="74" rx="36" fill="url(#qb-body)"/>
    <ellipse cx="46" cy="38" rx="20" ry="8" fill="#fff" opacity=".55" filter="url(#qb-soft)"/>
    <rect x="28" y="40" width="64" height="40" rx="20" fill="url(#qb-face)"/>
    <g class="qbi-eyes">
      <ellipse cx="47" cy="58" rx="6" ry="7.5" fill="#1b2433"/><ellipse cx="73" cy="58" rx="6" ry="7.5" fill="#1b2433"/>
      <circle cx="45" cy="55" r="2.3" fill="#fff"/><circle cx="71" cy="55" r="2.3" fill="#fff"/>
      <circle cx="49" cy="61" r="1" fill="#fff" opacity=".7"/><circle cx="75" cy="61" r="1" fill="#fff" opacity=".7"/>
    </g>
    <ellipse cx="36" cy="68" rx="5" ry="3" fill="#ff9fb8" opacity=".75"/><ellipse cx="84" cy="68" rx="5" ry="3" fill="#ff9fb8" opacity=".75"/>
    <path d="M54 69 Q60 74 66 69" stroke="#1b2433" stroke-width="2.4" fill="none" stroke-linecap="round" class="qbi-mouth"/>
    <rect x="40" y="84" width="40" height="13" rx="6.5" fill="url(#qb-belly)"/>
    <polyline points="45,93 52,90 57,92 64,87 71,88 75,86" fill="none" stroke="#3fe07a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    <ellipse cx="38" cy="104" rx="9" ry="6" fill="url(#qb-body)"/><ellipse cx="82" cy="104" rx="9" ry="6" fill="url(#qb-body)"/>
  </g>
</svg>`;
// 같은 SVG 를 두 번 쓰므로 그라데이션 id 가 겹치지 않게 접미사를 붙인다
const qbiSvg = (k) => QBI_TPL.replace(/qb-([a-z]+)/g, `qb-$1-${k}`);

const chat = { open: false, busy: false, thread: null, dragging: false, moved: false, log: [] };
const CHAT_KEY = "qtrader.chat.log";
function saveChatLog() {
  try { localStorage.setItem(CHAT_KEY, JSON.stringify({ thread: chat.thread, log: chat.log.slice(-60) })); } catch {}
}
function loadChatLog() {
  try { return JSON.parse(localStorage.getItem(CHAT_KEY)) || null; } catch { return null; }
}

function chatPos() {
  try { return JSON.parse(localStorage.getItem("qtrader.chat.pos")) || null; } catch { return null; }
}
function saveChatPos(p) { try { localStorage.setItem("qtrader.chat.pos", JSON.stringify(p)); } catch {} }

function buildChat() {
  const root = document.createElement("div");
  root.id = "qbi";
  root.innerHTML = `
    <div class="qbi-panel hidden" id="qbi-panel">
      <div class="qbi-head"><div class="qbi-mini">${qbiSvg("m")}</div>
        <div><b>큐비</b><span>AI 애널리스트 · 시세·계좌·회의 결과를 확인하고 답해요</span></div>
        <button class="qbi-x" id="qbi-clear" title="대화 지우기"><i data-lucide="eraser"></i></button>
        <button class="qbi-x" id="qbi-close" title="닫기"><i data-lucide="x"></i></button></div>
      <div class="qbi-log" id="chat-log"></div>
      <div class="qbi-quick" id="qbi-quick">
        <button data-q="오늘 시장 한 줄 요약해줘">오늘 시장 요약</button>
        <button data-q="지금 보고 있는 종목 분석해줘">이 종목 어때?</button>
        <button data-q="내 계좌 수익률이랑 위험한 종목 알려줘">내 계좌 점검</button>
        <button data-q="최근 AI 회의 결과 정리해줘">회의 결과 정리</button>
      </div>
      <form class="qbi-form" id="chat-form"><input id="chat-input" placeholder="무엇이든 물어보세요" autocomplete="off">
        <button class="primary" title="보내기"><i data-lucide="send"></i></button></form>
    </div>
    <button class="qbi-btn" id="qbi-btn" title="큐비에게 물어보기">${qbiSvg("b")}<span class="qbi-dot hidden" id="qbi-dot"></span></button>`;
  document.body.appendChild(root);
  const p = chatPos();
  if (p) { root.style.left = p.x + "px"; root.style.top = p.y + "px"; root.style.right = "auto"; root.style.bottom = "auto"; }
  clampChat();
  window.lucide && lucide.createIcons();
  const saved = loadChatLog();
  if (saved?.log?.length) {
    chat.thread = saved.thread;
    saved.log.forEach((m) => line(m.who, m.html, false));
    chat.log = saved.log;
  } else greet();
  $("#qbi-clear").onclick = () => {
    if (!confirm("큐비와 나눈 대화를 지울까요?")) return;
    chat.log = []; chat.thread = null; $("#chat-log").innerHTML = ""; saveChatLog(); greet();
  };

  const btn = $("#qbi-btn");
  let start = null;
  btn.addEventListener("pointerdown", (e) => {
    start = { x: e.clientX, y: e.clientY, l: root.offsetLeft, t: root.offsetTop };
    chat.moved = false;
    btn.setPointerCapture(e.pointerId);
  });
  btn.addEventListener("pointermove", (e) => {
    if (!start) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y;
    if (Math.abs(dx) + Math.abs(dy) > 5) chat.moved = true;
    if (!chat.moved) return;
    root.style.left = start.l + dx + "px"; root.style.top = start.t + dy + "px";
    root.style.right = "auto"; root.style.bottom = "auto";
    clampChat();
  });
  btn.addEventListener("pointerup", () => {
    if (chat.moved) saveChatPos({ x: root.offsetLeft, y: root.offsetTop });
    else toggleChat();
    start = null;
  });
  $("#qbi-close").onclick = () => toggleChat(false);
  $("#qbi-quick").addEventListener("click", (e) => { const b = e.target.closest("[data-q]"); if (b) send(b.dataset.q); });
  $("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); send($("#chat-input").value); });
  window.addEventListener("resize", clampChat);
}

function clampChat() {
  const root = $("#qbi");
  if (!root || root.style.left === "") return;
  const x = Math.min(Math.max(8, root.offsetLeft), innerWidth - 90), y = Math.min(Math.max(60, root.offsetTop), innerHeight - 100);
  root.style.left = x + "px"; root.style.top = y + "px";
  root.classList.toggle("left-side", x < innerWidth / 2);
  root.classList.toggle("top-side", y < innerHeight / 2);
}

function toggleChat(force) {
  chat.open = force ?? !chat.open;
  $("#qbi-panel").classList.toggle("hidden", !chat.open);
  $("#qbi-dot").classList.add("hidden");
  if (chat.open) {
    $("#chat-input").focus();
    window.animateIn && animateIn("#qbi-panel", { y: 12, duration: 260 });
  }
}

function greet() {
  line("bot", "안녕하세요, 큐비예요. 시세·차트·내 계좌·AI 회의 결과를 직접 확인해서 애널리스트처럼 답해 드릴게요. 저는 주문은 내지 않아요.", false);
}

function line(who, html, keep = true) {
  if (keep && !who.includes("typing")) { chat.log.push({ who, html }); saveChatLog(); }
  const log = $("#chat-log");
  log.insertAdjacentHTML("beforeend", `<div class="msg ${who}">${html}</div>`);
  log.scrollTop = log.scrollHeight;
}

function pageContext() {
  // 지금 보고 있는 화면을 같이 보내서 "이 종목"이 무엇인지 알게 한다
  const parts = [`현재 화면: ${({ mkt: "시장", stock: "종목", news: "뉴스", office: "AI 사무실", dash: "내 계좌", calc: "계산기", cfg: "설정" })[state.tab] || state.tab}`];
  const S = typeof st !== "undefined" ? st : null, O = typeof office !== "undefined" ? office : null;
  if (state.tab === "stock" && S?.symbol) parts.push(`보고 있는 종목: ${S.sum?.name || ""} (${S.symbol})`);
  if (state.tab === "office" && O?.run) parts.push(`열려 있는 회의: ${O.run.name || ""} (${O.run.symbol})`);
  return parts.join(" · ");
}

async function send(text) {
  text = (text || "").trim();
  if (!text || chat.busy) return;
  if (!chat.open) toggleChat(true);
  $("#chat-input").value = "";
  line("me", esc(text));
  chat.busy = true; window.chatBusy = true;
  $("#qbi").classList.add("thinking");
  line("bot typing", `<span class="dots"><i></i><i></i><i></i></span> 확인하는 중`);
  try {
    const r = await api("/api/assistant", { method: "POST", body: { text, thread_id: chat.thread, page: pageContext() } });
    chat.thread = r.thread_id;
    saveChatLog();
    $("#chat-log .typing")?.remove();
    const names = { market_overview: "시장 현황", rankings: "순위", quotes: "시세", chart_brief: "차트", portfolio: "내 계좌",
      recent_orders: "주문 내역", ai_decision: "AI 회의 결론", start_meeting: "회의 시작", web_search: "웹 검색" };
    const tools = r.tools.length ? `<div class="qbi-tools">${[...new Set(r.tools)].map((t) => `<span>${names[t] || t}</span>`).join("")}</div>` : "";
    const src = r.sources.length ? `<div class="sources">${r.sources.slice(0, 5).map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>`).join("")}</div>` : "";
    line("bot", `${tools}<div class="report-text">${window.mdLite ? mdLite(r.answer) : esc(r.answer)}</div>${src}`);
    if (r.tools.includes("start_meeting") && window.showRunById) { openTab("office"); }
  } catch (err) {
    $("#chat-log .typing")?.remove();
    line("bot", `<span class="down">${esc(err.message)}</span>`);
  }
  chat.busy = false; window.chatBusy = false;
  $("#qbi").classList.remove("thinking");
  if (!chat.open) $("#qbi-dot").classList.remove("hidden");
}

buildChat();
