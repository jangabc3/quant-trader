"use strict";
// AI 사무실: 회의 참석자를 픽셀 사무실에 그리고, 진행 상황을 말풍선으로 보여준다.

const W = 480, H = 300;
const office = { roles: [], run: null, selected: null, poll: null, frame: 0, thread: null };

// 참석자 자리 (x: 몸 중심, y: 발 위치). 비서는 회의 참석자가 아니라 대화 상대다
const SEATS = {
  chart: [36, 112], fund: [92, 112], news: [148, 112], flow: [204, 112],
  bull: [292, 116], lead: [362, 84], bear: [432, 116],
  trader: [80, 254],
  aggr: [206, 270], neut: [264, 230], cons: [322, 270],
  pm: [440, 236], assistant: [398, 290],
};
const S = 2; // 캐릭터 픽셀 배율
const ROOMS = [
  { x: 6, y: 18, w: 230, h: 116, floor: "#7b5332", label: "분석팀", lx: 14, ly: 22 },
  { x: 244, y: 18, w: 230, h: 116, floor: "#5e3f35", label: "토론실", lx: 252, ly: 22 },
  { x: 6, y: 146, w: 150, h: 148, floor: "#6f4c2e", label: "트레이딩 데스크", lx: 14, ly: 150 },
  { x: 164, y: 146, w: 200, h: 148, floor: "#4f4a5c", label: "리스크 위원회", lx: 172, ly: 150 },
  { x: 372, y: 146, w: 102, h: 148, floor: "#6b4a26", label: "대표실", lx: 380, ly: 150 },
];
const LOOKS = {
  teal: ["#1f8a8a", "#2a2a35"], navy: ["#2f4a8a", "#4a3020"], amber: ["#d08a1f", "#222"], rose: ["#c0507a", "#6a3a20"],
  green: ["#3a9a4a", "#3a2a1a"], red: ["#c03a3a", "#1a1a1a"], purple: ["#7a4ab0", "#2a1a30"], slate: ["#55606e", "#101418"],
  orange: ["#e0702a", "#5a2a10"], gray: ["#8a8f96", "#3a3a3a"], blue: ["#3a6ad0", "#1a2030"], gold: ["#c9a227", "#e8e0d0"],
  pink: ["#e07ab0", "#3a2010"],
};
const SKIN = "#f2c9a0";
const RATING_COLOR = { Buy: "#f0534e", Overweight: "#f08a5e", Hold: "#8b98a5", Underweight: "#6aa0ff", Sell: "#4c8dff" };
const RATING_KO = { Buy: "매수", Overweight: "비중확대", Hold: "보유", Underweight: "비중축소", Sell: "매도" };

const cv = $("#office");
const g = cv.getContext("2d");
g.imageSmoothingEnabled = false;
const px = (x, y, w, h, c) => { g.fillStyle = c; g.fillRect(Math.round(x), Math.round(y), w, h); };

function drawFloor() {
  px(0, 0, W, H, "#2a1d14");
  for (const r of ROOMS) {
    for (let y = r.y; y < r.y + r.h; y += 8) {
      for (let x = r.x + ((y / 8) % 2) * 8; x < r.x + r.w; x += 16) {
        px(x, y, Math.min(16, r.x + r.w - x), Math.min(8, r.y + r.h - y), r.floor);
        px(x, y, 1, Math.min(8, r.y + r.h - y), "rgba(0,0,0,.18)");
      }
      px(r.x, y + 7, r.w, 1, "rgba(0,0,0,.15)");
    }
    // 벽
    px(r.x - 3, r.y - 3, r.w + 6, 3, "#1a120c"); px(r.x - 3, r.y + r.h, r.w + 6, 3, "#1a120c");
    px(r.x - 3, r.y, 3, r.h, "#1a120c"); px(r.x + r.w, r.y, 3, r.h, "#1a120c");
    px(r.x, r.y, r.w, 10, "#3c2a1e");
  }
}

function plant(x, y) {
  px(x, y + 6, 6, 5, "#8a4a2a"); px(x - 1, y + 5, 8, 2, "#6a3a1a");
  px(x + 1, y, 4, 6, "#2f8a3a"); px(x - 1, y + 2, 3, 3, "#3aa04a"); px(x + 4, y + 1, 3, 3, "#3aa04a");
}

function monitor(x, y, on, t) {
  px(x, y, 16, 11, "#1b1f24"); px(x + 1, y + 1, 14, 8, on ? "#0f2a1a" : "#20262c");
  if (on) { for (let i = 0; i < 12; i++) px(x + 2 + i, y + 7 - ((i * 3 + t) % 6), 1, 1, i % 3 ? "#3fe07a" : "#f0534e"); }
  px(x + 7, y + 11, 2, 2, "#444");
}

// 책상: 몸 아랫부분을 가리고, 모니터는 얼굴을 피해 옆에 둔다 (mon: 책상 왼쪽 끝 기준 x 오프셋 목록)
function desk(x, y, w, on, t, mon = []) {
  px(x, y, w, 9, "#a0703f"); px(x, y + 9, w, 4, "#5b3a1e");
  px(x + 1, y + 1, w - 2, 1, "#b8864f");
  mon.forEach((dx, i) => monitor(x + dx, y - 10, on, t + i));
}

function person(x, y, look, status, t, facing = 1) {
  const [shirt, hair] = LOOKS[look] || LOOKS.gray;
  const bob = status === "working" ? (t % 2) : 0;
  const top = y - 22 * S + bob;
  const p = (dx, dy, w, h, c) => px(x + dx * S, top + dy * S, w * S, h * S, c);
  px(x - 6 * S, y - 2, 12 * S, 3, "rgba(0,0,0,.25)"); // 그림자
  p(-5, 9, 10, 9, shirt); // 몸
  p(-5, 9, 10, 1, "rgba(255,255,255,.15)");
  const arm = status === "working" ? t % 2 : 0;
  p(-7, 10 + arm, 2, 6, shirt); p(5, 10 + (status === "working" ? (t + 1) % 2 : 0), 2, 6, shirt);
  p(-7, 16 + arm, 2, 1, SKIN); p(5, 16, 2, 1, SKIN);
  p(-4, 18, 3, 3, "#2a2a35"); p(1, 18, 3, 3, "#2a2a35"); // 다리
  p(-4, 2, 8, 7, SKIN); // 얼굴
  p(-5, 0, 10, 3, hair); p(-5, 3, 1, 3, hair); p(4, 3, 1, 3, hair);
  const blink = t % 17 === 0;
  const f = facing < 0 ? -1 : 0;
  if (!blink) { p(-2 + f, 5, 1, 1, "#222"); p(1 + f, 5, 1, 1, "#222"); }
  p(-3, 6, 1, 1, "#f0a0a0"); p(2, 6, 1, 1, "#f0a0a0"); // 볼
  p(-1, 7, 2, 1, "#c0785a");
}

function lamp(x, y, status, t) {
  const c = status === "working" ? (t % 2 ? "#ffd34d" : "#a08020") : status === "done" ? "#3fe07a" : status === "error" ? "#f0534e" : "#555";
  px(x, y, 3, 3, c);
}

function agentStatus(id) {
  if (id === "assistant") return office.chatBusy ? "working" : "idle";
  return office.run?.agents?.[id]?.status || "idle";
}

function draw() {
  const t = office.frame;
  drawFloor();
  plant(224, 112); plant(10, 40); plant(462, 40); plant(140, 282); plant(352, 282); plant(462, 160);
  // 분석팀: 사람 -> 책상(아랫몸 가림) 순서
  ["chart", "fund", "news", "flow"].forEach((id) => {
    const [x, y] = SEATS[id];
    person(x, y, look(id), agentStatus(id), t);
    desk(x - 22, y - 14, 44, agentStatus(id) === "working", t, [28]);
    lamp(x - 20, y - 12, agentStatus(id), t);
  });
  // 토론실
  ["bull", "bear", "lead"].forEach((id) => { const [x, y] = SEATS[id]; person(x, y, look(id), agentStatus(id), t, id === "bear" ? -1 : 1); });
  px(318, 96, 88, 22, "#7a4a2a"); px(318, 118, 88, 4, "#4a2a14"); px(319, 97, 86, 1, "#9a6a4a");
  px(332, 102, 12, 8, "#e8e0d0"); px(380, 104, 12, 8, "#e8e0d0"); px(356, 100, 10, 10, "#c9a227");
  // 트레이딩 데스크
  { const [x, y] = SEATS.trader; person(x, y, look("trader"), agentStatus("trader"), t);
    desk(x - 34, y - 14, 68, agentStatus("trader") === "working", t, [1, 51]); }
  // 리스크 위원회
  ["aggr", "neut", "cons"].forEach((id) => { const [x, y] = SEATS[id]; person(x, y, look(id), agentStatus(id), t); });
  px(226, 242, 78, 20, "#6a4a3a"); px(226, 262, 78, 4, "#3a2418"); px(227, 243, 76, 1, "#8a6a5a");
  // 대표실
  { const [x, y] = SEATS.pm; person(x, y, "gold", agentStatus("pm"), t);
    desk(x - 26, y - 14, 52, agentStatus("pm") === "working", t, [0]); }
  { const [x, y] = SEATS.assistant; person(x, y, "pink", agentStatus("assistant"), t);
    px(x - 18, y - 14, 36, 8, "#c9a06a"); px(x - 18, y - 6, 36, 3, "#7a5a3a"); }
}

function look(id) { return (office.roles.find((r) => r.id === id) || {}).look || "gray"; }

// 말풍선·이름표 (HTML 로 얹어 한글을 선명하게)
function buildOverlay() {
  const ov = $("#office-overlay");
  const all = [...office.roles, { id: "assistant", name: "비서", title: "내 AI 비서" }];
  ov.innerHTML = ROOMS.map((r) => `<div class="room-label" style="left:${r.lx / W * 100}%;top:${r.ly / H * 100}%">◆ ${r.label} ◆</div>`).join("") +
    all.map((r) => {
      const [x, y] = SEATS[r.id];
      const edge = x < 60 ? "edge-l" : x > 420 ? "edge-r" : "";
      const wide = ["chart", "fund", "news", "flow"].includes(r.id) ? "" : "wide";
      return `<div class="agent ${edge} ${wide}" data-agent="${r.id}" style="left:${x / W * 100}%;top:${(y - 46) / H * 100}%;height:${46 / H * 100}%">
        <div class="bubble hidden"></div>
        <div class="hit"></div>
        <div class="tag"><b>${esc(r.name)}</b><span>${esc(r.title)}</span></div></div>`;
    }).join("");
}

function updateOverlay() {
  $$("#office-overlay .agent").forEach((el) => {
    const id = el.dataset.agent;
    const a = office.run?.agents?.[id];
    const b = el.querySelector(".bubble");
    let text = a?.bubble || "";
    if (id === "assistant") text = office.chatBusy ? "확인하는 중..." : "무엇이든 물어보세요";
    b.textContent = text;
    b.classList.toggle("hidden", !text);
    b.classList.toggle("working", agentStatus(id) === "working");
    el.classList.toggle("sel", office.selected === id);
  });
}

function loop() {
  office.frame = (office.frame + 1) % 1000;
  draw();
  setTimeout(() => requestAnimationFrame(loop), 160);
}

// 회의
function renderDecision() {
  const r = office.run;
  const el = $("#decision");
  if (!r) return;
  const f = r.final;
  const stage = r.status === "running" ? `진행 중: ${r.stage}` : r.status === "error" ? `오류: ${r.error}` : "완료";
  $("#meet-stage").textContent = `${r.name || r.symbol} · ${stage}`;
  if (!f) {
    el.innerHTML = `<h3>최종 결정 · ${esc(r.name || r.symbol)}</h3><p class="note">${esc(stage)}</p>
      <div class="progress">${office.roles.map((x) => `<span class="pdot ${r.agents?.[x.id]?.status || "idle"}" title="${esc(x.title)}"></span>`).join("")}</div>`;
    return;
  }
  const color = RATING_COLOR[f.rating] || "#8b98a5";
  el.innerHTML = `<h3>최종 결정 · ${esc(r.name || r.symbol)} <small>${esc(r.trade_date || "")}</small></h3>
    <div class="rating" style="border-color:${color};color:${color}">${RATING_KO[f.rating] || f.rating}<small>${esc(f.rating)}</small></div>
    <div class="conf"><span style="width:${Math.round(f.confidence * 100)}%;background:${color}"></span></div>
    <div class="note">확신도 ${Math.round(f.confidence * 100)}% · 손절 -${f.stop_loss_pct}% · 익절 +${f.take_profit_pct}% · ${f.horizon_days}거래일</div>
    <p class="headline">${esc(f.headline)}</p>
    <div class="cols"><div><b>근거</b><ul>${f.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
    <div><b>위험</b><ul>${f.risks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></div>
    <p class="note">토큰 입력 ${Number(r.tokens_in || 0).toLocaleString()} · 출력 ${Number(r.tokens_out || 0).toLocaleString()}. 투자 권유가 아닙니다.</p>`;
}

function renderReport() {
  const id = office.selected;
  const el = $("#report");
  if (!id) return;
  if (id === "assistant") { el.innerHTML = `<h3>비서</h3><p class="note">아래 대화창에서 비서에게 물어보세요.</p>`; return; }
  const role = office.roles.find((r) => r.id === id);
  const a = office.run?.agents?.[id];
  el.innerHTML = `<h3>${esc(role.name)} · ${esc(role.title)} ${role.web ? "<small>웹 검색</small>" : ""}</h3>` +
    (a?.report ? `<div class="report-text">${esc(a.report)}</div>` : `<p class="note">${a?.status === "working" ? "작성 중입니다..." : "아직 보고서가 없습니다."}</p>`) +
    (a?.sources?.length ? `<div class="sources"><b>출처</b>${a.sources.slice(0, 8).map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>`).join("")}</div>` : "");
}

async function showRun(id) {
  try {
    office.run = await api("/api/ai/run/" + id);
    renderDecision(); renderReport(); updateOverlay();
    clearTimeout(office.poll);
    if (office.run.status === "running") office.poll = setTimeout(() => showRun(id), 1500);
    else loadRuns();
  } catch (e) { $("#meet-stage").textContent = e.message; }
}

async function loadRuns() {
  try {
    const runs = await api("/api/ai/runs?limit=30");
    const sel = $("#meet-runs");
    const cur = office.run?.id;
    sel.innerHTML = `<option value="">지난 회의 보기</option>` + runs.map((r) =>
      `<option value="${r.id}" ${r.id === cur ? "selected" : ""}>${esc(r.trade_date)} ${esc(r.name || r.symbol)} · ${r.status === "running" ? "진행 중" : r.status === "error" ? "오류" : esc(r.rating_ko || "")}</option>`).join("");
    if (!office.run && runs.length) showRun(runs[0].id);
  } catch {}
}

window.startMeeting = async (symbol) => {
  symbol = (symbol || $("#meet-symbol").value).trim();
  if (!symbol) { $("#meet-symbol").focus(); return; }
  $("#meet-symbol").value = symbol;
  if (!confirm(`${symbol} AI 투자 회의를 시작합니다. 몇 분 걸리고 Claude API 비용이 듭니다. 계속할까요?`)) return;
  try {
    const r = await api("/api/ai/meeting", { method: "POST", body: { symbol } });
    office.selected = "chart";
    showRun(r.id);
  } catch (e) { alert(e.message); }
};
$("#meet-start").onclick = () => window.startMeeting();
$("#meet-symbol").addEventListener("keydown", (e) => { if (e.key === "Enter") window.startMeeting(); });
$("#meet-runs").onchange = () => { if ($("#meet-runs").value) showRun($("#meet-runs").value); };
$("#office-overlay").addEventListener("click", (e) => {
  const a = e.target.closest(".agent");
  if (!a) return;
  office.selected = a.dataset.agent;
  renderReport(); updateOverlay();
  if (office.selected === "assistant") $("#chat-input").focus();
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
    const tools = r.tools.length ? `<div class="note">확인한 것: ${[...new Set(r.tools)].join(", ")}</div>` : "";
    const src = r.sources.length ? `<div class="sources">${r.sources.map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>`).join("")}</div>` : "";
    chatLine("bot", `<div class="report-text">${esc(r.answer)}</div>${tools}${src}`);
    if (r.tools.includes("start_meeting")) loadRuns();
  } catch (err) { chatLine("bot", `<span class="down">${esc(err.message)}</span>`); }
  office.chatBusy = false; updateOverlay();
});

(async () => {
  office.roles = await api("/api/ai/roles");
  buildOverlay(); updateOverlay(); loop();
  chatLine("bot", "안녕하세요, 비서입니다. 시장 상황, 내 계좌, 종목 차트를 물어보시거나 \"삼성전자 회의 열어줘\"라고 해 보세요.");
})();
document.addEventListener("tab", (e) => { if (e.detail === "office") loadRuns(); });
