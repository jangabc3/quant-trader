"use strict";
// 처음 실행: 투자 성향 검사 -> 결과와 자동매매 기준 -> 3단계 시작 안내. 설정에서 다시 할 수 있다.

const ob = { meta: null, i: 0, answers: {}, result: null };

function obShell(inner) {
  let el = $("#onboard");
  if (!el) { el = document.createElement("div"); el.id = "onboard"; document.body.appendChild(el); }
  el.innerHTML = `<div class="ob-box">${inner}</div>`;
  el.classList.remove("hidden");
  icons();
  window.anime && anime({ targets: "#onboard .ob-box", opacity: [0, 1], translateY: [14, 0], duration: 300, easing: "easeOutQuad" });
}
function obClose() { $("#onboard")?.classList.add("hidden"); }

function obWelcome() {
  obShell(`<div class="ob-hero"><div class="ob-mascot">${typeof qbiSvg === "function" ? qbiSvg("o") : ""}</div>
    <h2>QTrader 에 오신 걸 환영해요</h2>
    <p>자동매매를 시작하기 전에 <b>투자 성향</b>을 알려 주세요. 증권사 앱의 투자 성향 진단처럼 8개 질문에 답하면,
    사람마다 다른 기준(한 종목 비중, 하루 손실 한도, 매수 조건)으로 자동매매가 맞춰집니다.</p>
    <p class="note">1분 정도 걸립니다. 결과는 설정에서 언제든 다시 바꿀 수 있어요.</p></div>
    <div class="ob-actions"><button id="ob-skip">나중에 할게요</button><button class="primary" id="ob-go"><i data-lucide="arrow-right"></i> 시작하기</button></div>`);
  $("#ob-go").onclick = () => { ob.i = 0; obQuestion(); };
  $("#ob-skip").onclick = async () => { await api("/api/profile/skip", { method: "POST" }).catch(() => {}); obClose(); };
}

function obQuestion() {
  const qs = ob.meta.questions, q = qs[ob.i];
  obShell(`<div class="ob-prog"><i style="width:${(ob.i / qs.length) * 100}%"></i></div>
    <div class="note">질문 ${ob.i + 1} / ${qs.length}</div><h2>${esc(q.q)}</h2>
    <div class="ob-opts">${q.a.map(([t], k) => `<button class="ob-opt ${ob.answers[q.id] === k ? "on" : ""}" data-k="${k}">${esc(t)}</button>`).join("")}</div>
    <div class="ob-actions"><button id="ob-back" ${ob.i ? "" : "disabled"}><i data-lucide="arrow-left"></i> 이전</button></div>`);
  $$(".ob-opt").forEach((b) => b.onclick = async () => {
    ob.answers[q.id] = Number(b.dataset.k);
    if (ob.i < qs.length - 1) { ob.i++; obQuestion(); }
    else await obSubmit();
  });
  $("#ob-back").onclick = () => { if (ob.i) { ob.i--; obQuestion(); } };
}

function criteriaHtml(list) {
  return `<div class="crit">${list.map((c) => `<div class="crit-row"><i data-lucide="${c.icon}"></i><span>${esc(c.k)}</span><b>${esc(c.v)}</b><em>${esc(c.why)}</em></div>`).join("")}</div>`;
}
window.criteriaHtml = criteriaHtml;

async function obSubmit() {
  try {
    ob.result = await api("/api/profile", { method: "POST", body: { answers: ob.answers } });
  } catch (e) { alert(e.message); return; }
  const r = ob.result;
  const idx = ob.meta.types.findIndex((t) => t.name === r.type);
  obShell(`<div class="note">검사 결과</div><h2>당신은 <span class="ob-type t${idx}">${esc(r.type)}</span> · ${esc(r.style)}</h2>
    <div class="ob-scale">${ob.meta.types.map((t, k) => `<span class="${k === idx ? "on" : ""}">${esc(t.name)}</span>`).join("")}</div>
    <p>${esc(r.desc)}</p><h3>이렇게 자동매매합니다</h3>${criteriaHtml(r.criteria)}
    <div class="ob-actions"><button id="ob-redo"><i data-lucide="rotate-ccw"></i> 다시 하기</button><button class="primary" id="ob-next"><i data-lucide="check"></i> 이 기준으로 시작</button></div>`);
  $("#ob-redo").onclick = () => { ob.i = 0; ob.answers = {}; obQuestion(); };
  $("#ob-next").onclick = obGuide;
  state.config = await api("/api/config").catch(() => state.config);
}

function obGuide() {
  const k = state.status?.keys || {};
  const step = (n, done, title, desc, btn, act) => `<div class="ob-step ${done ? "done" : ""}"><span class="num">${done ? "✓" : n}</span>
    <div><b>${title}</b><p class="note">${desc}</p></div>${btn ? `<button data-act="${act}">${btn}</button>` : ""}</div>`;
  obShell(`<h2>3단계로 시작해요</h2>
    ${step(1, k.toss && k.anthropic, "API 키 연결", "토스증권(시세·주문)과 Claude(AI 회의·큐비) 키를 설정에 저장합니다. 이 PC에만 저장돼요.", "설정 열기", "cfg")}
    ${step(2, state.status?.mode === "paper", "가상매매로 먼저", "처음 몇 주는 실제 시세로 내 PC 안에서만 체결되는 가상매매로 결과를 확인하세요. 지금 가상매매 모드입니다.", "", "")}
    ${step(3, false, "첫 AI 투자 회의", "관심 있는 종목을 골라 AI 회의를 열면, 사는 가격·손절·익절·수량과 근거가 나옵니다.", "AI 사무실로", "office")}
    <div class="ob-actions"><button class="primary" id="ob-done">시작하기</button></div>`);
  $$("#onboard [data-act]").forEach((b) => b.onclick = () => { obClose(); openTab(b.dataset.act); });
  $("#ob-done").onclick = obClose;
}

window.openSurvey = async () => {
  ob.meta = await api("/api/profile");
  ob.answers = { ...(ob.meta.investor?.answers || {}) };
  obWelcome();
};

// 설정·내 계좌의 '매매 기준' 카드
window.renderCriteria = async () => {
  try {
    const m = await api("/api/profile");
    const inv = m.investor;
    const head = inv?.type ? `<div class="inv-head"><span class="ob-type">${esc(inv.type)}</span><span class="note">${esc(inv.style || "")} · ${esc((inv.decided_at || "").replace("T", " "))} 검사</span>
      <button id="survey-again"><i data-lucide="rotate-ccw"></i> 투자 성향 다시 검사</button></div>`
      : `<div class="inv-head"><span class="note">아직 투자 성향 검사를 하지 않았어요. 기본 기준을 씁니다.</span><button class="primary" id="survey-again">투자 성향 검사하기</button></div>`;
    $$(".criteria-box").forEach((el) => { el.innerHTML = head + criteriaHtml(m.criteria); });
    $$("#survey-again").forEach((b) => b.onclick = () => window.openSurvey());
    icons();
  } catch {}
};

(async () => {
  try {
    ob.meta = await api("/api/profile");
    if (!ob.meta.investor) obWelcome();
  } catch {}
})();
document.addEventListener("tab", (e) => { if (["cfg", "dash"].includes(e.detail)) window.renderCriteria(); });
