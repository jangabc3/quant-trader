"use strict";
// 처음 실행: 투자 성향 검사 -> 결과와 자동매매 기준 -> 3단계 시작 안내. 설정에서 다시 할 수 있다.

const ob = { meta: null, i: 0, answers: {}, result: null };

function obShell(inner) {
  let el = $("#onboard");
  if (!el) { el = document.createElement("div"); el.id = "onboard"; document.body.appendChild(el); }
  el.innerHTML = `<div class="ob-box">${inner}</div>`;
  el.classList.remove("hidden");
  icons();
  window.animateIn && animateIn("#onboard .ob-box", { y: 14, duration: 300 });
}
function obClose() { $("#onboard")?.classList.add("hidden"); }

function obWelcome() {
  obShell(`<div class="ob-hero"><div class="ob-mascot">${typeof qbiSvg === "function" ? qbiSvg("o") : ""}</div>
    <h2>QTrader 에 오신 걸 환영해요</h2>
    <p>자동매매를 시작하기 전에 <b>투자 성향</b>을 알려 주세요. 증권사 앱의 투자 성향 진단처럼 8개 질문에 답하면,
    사람마다 다른 기준(한 종목 비중, 하루 손실 한도, 매수 조건)으로 자동매매가 맞춰집니다.</p>
    <p class="note">1분 정도 걸립니다. 결과는 설정에서 언제든 다시 바꿀 수 있어요.</p></div>
    <div class="ob-actions">${ob.meta.investor?.type ? `<button id="ob-cancel">취소</button>` : `<span class="note">검사를 마쳐야 자동매매를 켤 수 있어요</span>`}
      <button class="primary" id="ob-go"><i data-lucide="arrow-right"></i> 검사 시작</button></div>`);
  $("#ob-go").onclick = () => { ob.i = 0; obQuestion(); };
  if ($("#ob-cancel")) $("#ob-cancel").onclick = obClose;
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

const CRIT_GROUPS = [["money", "wallet", "돈 관리", "얼마나 사고, 하루에 얼마까지 잃어도 되는지"],
  ["buy", "search-check", "살 때 조건", "필수 조건은 꼭 지키고, 추가 조건은 조금 덜 맞으면 적게 사요"], ["sell", "shield-check", "팔 때 · 안전장치", "손실을 작게 끊는 규칙"]];
function criteriaHtml(list) {
  return `<div class="crit-groups">${CRIT_GROUPS.map(([g, icon, title, sub]) => {
    const rows = list.filter((c) => (c.g || "buy") === g);
    return rows.length ? `<section class="crit-card"><header><i data-lucide="${icon}"></i><div><b>${title}</b><span>${sub}</span></div></header>
      ${rows.map((c) => `<div class="crit-item" title="${esc(c.why)}"><i data-lucide="${c.icon}"></i><div><span>${esc(c.k)}</span><em>${esc(c.why)}</em></div><b>${esc(c.v)}</b></div>`).join("")}</section>` : "";
  }).join("")}</div>`;
}
window.criteriaHtml = criteriaHtml;

async function obSubmit() {
  try {
    ob.result = await api("/api/profile", { method: "POST", body: { answers: ob.answers } });
  } catch (e) { toast(e.message); return; }
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
  const paper = state.status?.mode === "paper";
  const steps = [
    [k.toss && k.anthropic, "key-round", "키 연결", "토스증권(시세·주문)과 Claude(AI 회의) 키를 저장합니다. 이 PC 안에만 보관돼요.", "설정 열기", "cfg"],
    [paper, "flask-conical", "가상매매로 연습", paper ? "지금 가상매매 모드예요. 실제 시세로 내 PC 안에서만 체결됩니다. 2~4주 결과를 먼저 보세요."
      : "지금 실전 모드예요. 처음에는 설정 → 운용 모드에서 가상매매를 권합니다.", paper ? "" : "모드 바꾸기", "cfg"],
    [false, "building-2", "첫 AI 투자 회의", "종목을 골라 회의를 열면 사는 가격·손절·익절·수량과 근거가 나와요.", "AI 사무실로", "office"],
  ];
  const doneN = steps.filter((x) => x[0]).length;
  const tail = (done, btn, act) => done ? `<span class="chip-ok">완료</span>`
    : btn ? `<button data-act="${act}">${btn} <i data-lucide="chevron-right"></i></button>` : "";
  obShell(`<div class="note">시작 준비 ${doneN} / 3</div><h2>3단계로 시작해요</h2>
    <div class="ob-prog"><i style="width:${doneN / 3 * 100}%"></i></div>
    <ol class="ob-steps">${steps.map(([done, icon, title, desc, btn, act], n) => `<li class="ob-step ${done ? "done" : ""}">
      <span class="num">${done ? `<i data-lucide="check"></i>` : n + 1}</span>
      <div class="ob-step-body"><b><i data-lucide="${icon}"></i>${title}</b><p>${desc}</p></div>${tail(done, btn, act)}</li>`).join("")}</ol>
    <div class="ob-actions"><button class="primary" id="ob-done"><i data-lucide="check"></i> 확인했어요</button></div>`);
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
    if (!ob.meta.investor?.type) obWelcome();
  } catch {}
})();
document.addEventListener("tab", (e) => { if (["cfg", "dash"].includes(e.detail)) window.renderCriteria(); });
