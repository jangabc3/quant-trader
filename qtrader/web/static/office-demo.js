"use strict";
// AI 사무실 시연: Claude 를 부르지 않고, 회의가 어떤 순서로 흘러가는지 움직임만 보여 준다.
// 말풍선·보고서 문장은 예시이며 실제 분석이 아니다.

const DEMO_SAY = {
  chart: ["일봉·이동평균선 확인 중...", "200일선 위, 추세 상승"], fund: ["PER·ROE 비교 중...", "업종 평균보다 저평가"],
  earn: ["지난 분기 실적 읽는 중...", "영업이익 예상치 상회"], news: ["오늘 뉴스 확인 중...", "호재 3건, 악재 1건"],
  flow: ["외국인·기관 수급 보는 중...", "외국인 5일 연속 순매수"], checker: ["주장 18개 원자료 대조 중...", "확인 15 · 불확실 3 · 틀림 0"],
  bull: ["실적이 계속 좋아질 근거가 있어요", "수요가 꺾일 신호는 아직 없어요"], bear: ["단기 급등, 과열이 걱정돼요", "환율이 바뀌면 이익이 줄 수 있어요"],
  lead: ["양쪽 근거를 저울질하는 중...", "판정: 비중확대 쪽"], trader: ["진입가·손절·익절 계산 중...", "손절 -6% · 익절 +15%"],
  aggr: ["비중을 늘려도 됩니다", "의견: 확대"], neut: ["손익비 2.5 : 1, 무난합니다", "의견: 유지"], cons: ["하락장이면 절반만", "의견: 축소"],
  pm: ["최종 결정 중...", "비중확대 · 확신 70%"],
};
const DEMO_STEPS = [
  // [초, 단계, 일하는 사람, 끝난 사람, 서류 넘기기]
  [0, "analysts", ["chart", "fund", "earn", "news", "flow"], [], []],
  [7, "check", ["checker"], ["chart", "fund", "earn", "news", "flow"], [["chart", "checker"], ["fund", "checker"], ["earn", "checker"], ["news", "checker"], ["flow", "checker"]]],
  [12, "debate", ["bull", "bear"], ["checker"], [["checker", "bull"], ["checker", "bear"]]],
  [15, "debate", ["bull", "bear"], [], []],
  [19, "lead", ["lead"], ["bull", "bear"], [["bull", "lead"], ["bear", "lead"]]],
  [23, "trader", ["trader"], ["lead"], [["lead", "trader"]]],
  [27, "risk", ["aggr", "neut", "cons"], ["trader"], [["trader", "aggr"], ["trader", "neut"], ["trader", "cons"]]],
  [32, "pm", ["pm"], ["aggr", "neut", "cons"], [["aggr", "pm"], ["neut", "pm"], ["cons", "pm"]]],
  [37, "end", [], ["pm"], []],
];

function demoRun() {
  const agents = Object.fromEntries(office.roles.map((r) => [r.id, { status: "idle", bubble: "", report: "" }]));
  return { id: "demo", demo: true, symbol: "005930", name: "삼성전자 (시연)", market: "KR", mode: "fast", status: "running",
    stage: "분석팀 보고서", started_at: new Date().toISOString(), finished_at: null, calls: 0, cost_usd: 0,
    stages: Object.fromEntries(office.stages.map((s) => [s.key, "idle"])), agents, handoffs: [] };
}

// 시연이 끝나면 실제 결과 화면과 같은 모양으로 보여 줄 예시 결과 (현재가 기준으로 숫자를 맞춘다)
function demoResult(r, last) {
  const k = (x) => Math.round(last * x / 100) * 100 || Math.round(last * x);
  const lo = k(0.99), hi = k(1.01), mid = (lo + hi) / 2;
  r.facts = { close: last, rsi14: 58, vs_sma20: 3.1, from_high: -7.4, vol_annual: 29, vol_ratio: 1.4 };
  r.final = { rating: "Overweight", confidence: 0.7, horizon_days: 20, entry_low: lo, entry_high: hi, stop_loss_pct: 6, take_profit_pct: 15,
    headline: "실적 회복과 외국인 매수가 이어지고 있어 비중을 조금 늘릴 만하다 (예시)",
    summary: ["메모리 가격이 오르며 다음 분기 이익이 늘어날 가능성이 높다", "외국인이 5일 연속 순매수 중이고 200일선 위에서 추세가 살아 있다",
      "단기 급등으로 과열 신호가 일부 있어 한 번에 다 사지 않고 비중을 나눈다"],
    thesis: "업황 바닥을 지나 이익이 다시 늘어나는 구간이다. 시장이 아직 이익 증가를 다 반영하지 않았고, 수급과 추세가 같은 방향을 가리킨다. 다만 환율과 단기 과열을 감안해 비중확대(일부 매수)로 접근한다.",
    reasons: ["다음 분기 영업이익 예상치가 3개월 연속 올라감", "PER 이 5년 평균보다 낮아 가격 부담이 적음", "외국인·기관 동반 순매수로 수급이 받쳐 줌"],
    short_args: [{ claim: "단기 급등으로 RSI 가 높아 조정이 올 수 있다", rebuttal: "RSI 58 로 과열(75) 전이고, 손절 -6% 로 손실을 제한한다" },
      { claim: "환율이 내리면 수출 이익이 줄어든다", rebuttal: "환율 민감도는 이익의 일부이며 가격 상승 효과가 더 크다" }],
    trust: ["이익 추정치 상향", "200일선 위 상승 추세"], worry: ["환율 하락", "경쟁사 증설로 가격 경쟁"], watch: ["다음 달 수출 통계", "외국인 순매수 지속 여부"],
    scenarios: { bear: { price: k(0.9), probability: 0.25, condition: "업황 회복이 늦어지고 외국인이 팔 때" },
      base: { price: k(1.08), probability: 0.5, condition: "예상대로 이익이 늘 때" }, bull: { price: k(1.2), probability: 0.25, condition: "가격 상승이 예상보다 클 때" } },
    bull_score: 64, risk_votes: { aggr: "확대", neut: "유지", cons: "축소" } };
  r.check = { summary: "주요 주장 18개 중 15개 확인, 틀린 주장 없음",
    scores: { technical: 68, fundamental: 72, valuation: 64, earnings: 75, news: 58, flow: 70 },
    valuation: [{ metric: "PER", current: 11.2, hist_avg: 13.5, peer_avg: 15.1, unit: "배" }, { metric: "PBR", current: 1.2, hist_avg: 1.4, peer_avg: 1.8, unit: "배" }],
    fact_checks: [{ status: "확인됨", claim: "외국인 5일 연속 순매수", detail: "투자자별 매매 자료와 일치", source: "토스증권 수급 (예시)" },
      { status: "확인됨", claim: "200일선 위에 있음", detail: "종가가 200일 이동평균보다 높음", source: "일봉 계산" },
      { status: "불확실", claim: "다음 분기 이익 20% 증가", detail: "증권사 추정치마다 차이가 큼", source: "뉴스 (예시)" }],
    key_questions: [{ question: "왜 지금 사나요?", answer: "이익이 늘기 시작하는 초입이고 수급과 추세가 같은 방향이라서입니다.", source: "팀장 판정 (예시)" },
      { question: "틀리면 얼마나 잃나요?", answer: "손절가 -6% 에서 자동으로 팔아 최대 손실을 미리 정해 둡니다.", source: "트레이더 실행안 (예시)" }] };
  r.debate = [{ role: "bull", round: 1, text: "이익 추정치가 계속 올라가고 있습니다. 수요가 꺾일 신호는 아직 없습니다." },
    { role: "bear", round: 1, text: "최근 2주 급등으로 단기 과열입니다. 환율이 내리면 이익이 줄 수 있습니다." }];
  const qty = Math.max(1, Math.floor(1000000 / mid));
  r.plan = { currency: "KRW", entry_low: lo, entry_high: hi, entry: mid, stop: k(0.94), stop_pct: 6, take: k(1.15), take_pct: 15,
    qty, amount: qty * mid, max_loss: qty * (mid - k(0.94)), max_gain: qty * (k(1.15) - mid), horizon_days: 20 };
  r.checklist = { passed: true, rules: [
    ["AI 최종 의견이 매수 계열", "Overweight", "Buy 또는 Overweight"], ["AI 확신도", "70%", "65% 이상"], ["손익비 (익절폭 ÷ 손절폭)", "2.5 : 1", "2.0 : 1 이상"],
    ["장기 추세 (종가 > 200일선)", "위", "200일선 위"], ["12개월 수익률 양수", "+18%", "0% 초과"], ["과열 추격 아님 (RSI)", "58", "75 미만"],
    ["변동성 과다 아님", "29%", "연 50% 이하"], ["팩트체크 통과 (틀린 주장 없음)", "확인 15/18, 틀림 0", "확인 60% 이상, 틀림 0"],
    ["시장 국면 (지수 > 200일선)", "상승 국면", "하락 국면이면 비중 절반"]].map(([name, value, need]) => ({ name, value, need, ok: true, ref: "" })) };
  if (typeof RES !== "undefined") RES.chartFor = null;
  r.trade_date = new Date().toLocaleDateString("sv-SE");
  window.renderResult(r);
  $("#result").insertAdjacentHTML("afterbegin", `<div class="panel demo-note"><h3><i data-lucide="clapperboard"></i> 회의 결과 예시 (시연)</h3>
    <p class="note">아래는 실제 회의가 끝났을 때 나오는 결과 화면의 <b>예시</b>입니다. 숫자와 문장은 지어낸 것이며 투자 판단에 쓰면 안 됩니다.
    위에서부터 <b>최종 의견 → 차트 → 실행 계획(얼마에 사고 팔지) → 체크리스트 → 시나리오 → 근거·반대 논리 → 팩트체크</b> 순서로 읽으면 됩니다.</p></div>`);
  icons();
}

window.startDemo = async () => {
  let last = 70000;
  try { last = (await api("/api/stock/005930/summary")).price || last; } catch {}
  office.demoLast = last;
  clearTimeout(office.poll);
  (office.demoTimers || []).forEach(clearTimeout);
  office.demoTimers = [];
  office.run = demoRun(); office.seenHandoffs = 0; office.papers = []; office.popClosed = null; office.selected = "chart";
  $("#result").innerHTML = `<div class="panel demo-note"><h3><i data-lucide="clapperboard"></i> 회의 시연 중</h3>
    <p class="note">Claude 를 부르지 않고 회의 순서와 움직임만 보여 주는 시연입니다. 말풍선과 보고서 문장은 <b>예시</b>이며 실제 분석이 아닙니다.
    실제 회의는 Claude API 키를 저장한 뒤 종목을 입력하고 'AI 투자 회의 시작'을 누르면 열립니다.</p></div>`;
  icons();
  $("#office-wrap").scrollIntoView({ behavior: "smooth", block: "center" });
  const keys = office.stages.map((s) => s.key);
  DEMO_STEPS.forEach(([sec, stage, work, done, hand], n) => {
    office.demoTimers.push(setTimeout(() => {
      const r = office.run;
      if (!r || r.id !== "demo") return;
      done.forEach((id) => {
        const a = r.agents[id];
        a.status = "done"; a.bubble = DEMO_SAY[id]?.[1] || "";
        a.report = `### ${office.roles.find((x) => x.id === id)?.title} 보고 (시연용 예시)\n\n- ${DEMO_SAY[id]?.[1] || ""}\n- 실제 회의에서는 근거 숫자와 출처 링크가 여기에 붙습니다.`;
      });
      work.forEach((id) => {
        const a = r.agents[id];
        a.status = "working"; a.bubble = DEMO_SAY[id]?.[n === 3 ? 1 : 0] || "작업 중...";
      });
      r.handoffs.push(...hand.map(([from, to]) => ({ from, to })));
      r.calls += work.length ? 1 : 0;
      if (stage === "end") {
        Object.keys(r.stages).forEach((k) => (r.stages[k] = "done"));
        r.status = "done"; r.stage = "완료"; r.finished_at = new Date().toISOString();
        demoResult(r, office.demoLast);
      } else {
        keys.forEach((k, i) => { r.stages[k] = i < keys.indexOf(stage) ? "done" : k === stage ? "working" : r.stages[k]; });
        r.stage = office.stages.find((s) => s.key === stage)?.name || stage;
      }
      renderStages(); updateOverlay(); renderReport();
    }, sec * 1000));
  });
};

const demoBtn = document.createElement("button");
demoBtn.id = "meet-demo";
demoBtn.title = "Claude 를 부르지 않고 회의가 흘러가는 모습만 봅니다 (비용 없음)";
demoBtn.innerHTML = `<i data-lucide="clapperboard"></i> 회의 시연 보기`;
$("#meet-start").after(demoBtn);
demoBtn.onclick = () => window.startDemo();
// 지난 회의를 고르면 시연은 멈춘다
$("#meet-runs").addEventListener("change", () => (office.demoTimers || []).forEach(clearTimeout));
icons();
