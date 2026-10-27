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

window.startDemo = () => {
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
        r.final = { rating: "Overweight", confidence: 0.7 };
        r.plan = { currency: "KRW", entry_low: 70000, entry_high: 71000, stop: 66300, stop_pct: 6, take: 81200, take_pct: 15,
          qty: 14, amount: 987000, max_loss: 61600, max_gain: 148400, horizon_days: 20 };
        r.checklist = { passed: true };
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
