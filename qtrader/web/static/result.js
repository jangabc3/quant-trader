"use strict";
// 분석 결과 대시보드: 회의 결과를 "왜 사야(팔아야) 하는지" 한눈에 보이게 그린다.
// 구성은 타민더마켓 가이드의 리포트·딥리서치 항목(요약, 투자 논문, 숏 논거와 반박, 시나리오,
// 밸류에이션 비교, Trust/Worry/Watch, 팩트체크, 추가 질문)을 따른다.

const RES = { chartFor: null, chart: null, series: {} };
const R_COLOR = { Buy: "#f04452", Overweight: "#f08a5e", Hold: "#8b98a5", Underweight: "#5a9bff", Sell: "#3182f6" };
const R_KO = { Buy: "매수", Overweight: "비중확대", Hold: "보류", Underweight: "비중축소", Sell: "매도" };
const SCORE_KO = { technical: "기술", fundamental: "재무", valuation: "밸류", earnings: "실적", news: "뉴스", flow: "수급" };
const VOTE_COLOR = { 확대: "#f04452", 유지: "#8b98a5", 축소: "#5a9bff", 관망: "#c9a227" };

const fmtPx = (v, m) => v == null || isNaN(v) ? "-" : m === "US" ? "$" + Number(v).toFixed(2) : Math.round(v).toLocaleString() + "원";
const upPct = (to, from) => from ? (to / from - 1) : 0;
// AI 가 통화 단위를 헷갈린 가격(현재가의 0.3배 미만, 3배 초과)은 그리지 않고 경고한다
const sane = (p, last) => p > 0 && (!last || (p > last * 0.3 && p < last * 3));

function radar(scores) {
  const keys = Object.keys(SCORE_KO);
  const cx = 130, cy = 120, R = 88;
  const pt = (i, v) => {
    const a = -Math.PI / 2 + i * 2 * Math.PI / keys.length;
    return [cx + Math.cos(a) * R * v / 100, cy + Math.sin(a) * R * v / 100];
  };
  const grid = [25, 50, 75, 100].map((v) => `<polygon points="${keys.map((_, i) => pt(i, v).join(",")).join(" ")}" fill="none" stroke="#2c3743"/>`).join("");
  const axes = keys.map((k, i) => {
    const [x, y] = pt(i, 100), [lx, ly] = pt(i, 122);
    return `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="#2c3743"/><text x="${lx}" y="${ly + 4}" text-anchor="middle" fill="#c7d0d9" font-size="12">${SCORE_KO[k]} ${Math.round(scores[k] ?? 0)}</text>`;
  }).join("");
  const poly = keys.map((k, i) => pt(i, Math.max(0, Math.min(100, scores[k] ?? 0))).join(",")).join(" ");
  return `<svg viewBox="0 0 260 245" class="radar">${grid}${axes}<polygon points="${poly}" fill="rgba(49,130,246,.3)" stroke="#3182f6" stroke-width="2"/></svg>`;
}

function scenarioBlock(f, last, m) {
  const sc = f.scenarios || {};
  const order = [["bear", "약세", "#3182f6"], ["base", "기본", "#8b98a5"], ["bull", "강세", "#f04452"]]
    .filter(([k]) => sc[k] && sane(sc[k].price, last));
  if (!order.length) return `<p class="note">시나리오 가격이 현재가와 맞지 않아 표시하지 않습니다 (AI 가 통화 단위를 잘못 썼을 수 있습니다).</p>`;
  const prices = order.map(([k]) => sc[k]?.price || last).concat([last]);
  const lo = Math.min(...prices) * 0.97, hi = Math.max(...prices) * 1.03;
  const pos = (v) => ((v - lo) / (hi - lo) * 100).toFixed(1);
  const ev = order.reduce((s, [k]) => s + (sc[k]?.price || 0) * (sc[k]?.probability || 0), 0);
  const psum = order.reduce((s, [k]) => s + (sc[k]?.probability || 0), 0) || 1;
  const evn = ev / psum;
  return `<div class="scen-axis">
      <div class="scen-track"></div>
      <div class="scen-mark now" style="left:${pos(last)}%"><span>현재 ${fmtPx(last, m)}</span></div>
      ${order.map(([k, n, c]) => sc[k] ? `<div class="scen-mark" style="left:${pos(sc[k].price)}%;--c:${c}"><span>${n} ${fmtPx(sc[k].price, m)}</span></div>` : "").join("")}
      <div class="scen-mark ev" style="left:${pos(evn)}%"><span>기대값 ${fmtPx(evn, m)}</span></div>
    </div>
    <table class="scen"><thead><tr><th>시나리오</th><th class="r">가격</th><th class="r">현재 대비</th><th class="r">확률</th><th>조건</th></tr></thead><tbody>
    ${order.map(([k, n, c]) => sc[k] ? `<tr><td><b style="color:${c}">${n}</b></td><td class="r">${fmtPx(sc[k].price, m)}</td>
      <td class="r ${cls(upPct(sc[k].price, last))}">${pct(upPct(sc[k].price, last))}</td>
      <td class="r"><div class="pbar"><span style="width:${Math.round((sc[k].probability || 0) * 100)}%;background:${c}"></span></div>${Math.round((sc[k].probability || 0) * 100)}%</td>
      <td>${esc(sc[k].condition)}</td></tr>` : "").join("")}
    <tr class="ev-row"><td><b>확률 가중 기대값</b></td><td class="r"><b>${fmtPx(evn, m)}</b></td><td class="r ${cls(upPct(evn, last))}"><b>${pct(upPct(evn, last))}</b></td><td colspan="2" class="note">각 시나리오 가격 × 확률의 합 (AI 추정)</td></tr>
    </tbody></table>`;
}

function valuationBlock(vals) {
  if (!vals?.length) return `<p class="note">밸류에이션 자료를 찾지 못했습니다.</p>`;
  return `<div class="val-grid">${vals.map((v) => {
    const nums = [v.current, v.hist_avg, v.peer_avg].filter((x) => x != null && !isNaN(x));
    const max = Math.max(...nums, 1) * 1.15;
    const bar = (x, c, n) => x == null ? `<div class="vrow"><span>${n}</span><i class="note">자료 없음</i></div>`
      : `<div class="vrow"><span>${n}</span><div class="vbar"><b style="width:${(x / max * 100).toFixed(1)}%;background:${c}"></b></div><em>${Number(x).toLocaleString(undefined, { maximumFractionDigits: 2 })}${esc(v.unit || "")}</em></div>`;
    const ref = v.hist_avg ?? v.peer_avg;
    const higherCheap = /수익률|yield/i.test(v.metric);
    let tag = "";
    if (v.current != null && ref != null) {
      const cheap = higherCheap ? v.current > ref : v.current < ref;
      tag = `<span class="vtag ${cheap ? "cheap" : "rich"}">${cheap ? "평균보다 싸다" : "평균보다 비싸다"}</span>`;
    }
    return `<div class="vcard"><div class="vhead"><b>${esc(v.metric)}</b>${tag}</div>
      ${bar(v.current, "#ffd34d", "현재")}${bar(v.hist_avg, "#6b7785", "5년 평균")}${bar(v.peer_avg, "#3182f6", "경쟁사")}
      ${v.note ? `<div class="note">${esc(v.note)}</div>` : ""}</div>`;
  }).join("")}</div>`;
}

// 차트: 기본은 '간단히' (캔들 + 20일선 + 매수·손절·익절 세 줄). '자세히'를 누르면 60·120일선, 지지·저항, 시나리오까지
async function drawChart(run, f, detail = RES.detail) {
  const el = $("#res-chart");
  if (!el || !window.LightweightCharts) return;
  RES.detail = detail;
  $$("#res-chart-mode button").forEach((b) => b.classList.toggle("on", (b.dataset.v === "detail") === detail));
  let data = RES.data?.sym === run.symbol ? RES.data : null;
  if (!data) {
    try { data = await api(`/api/ai/chart/${encodeURIComponent(run.symbol)}?days=180`); data.sym = run.symbol; RES.data = data; }
    catch (e) { el.innerHTML = `<p class="note">${esc(e.message)}</p>`; return; }
  }
  el.innerHTML = "";
  const kr = run.market !== "US";
  const chart = LightweightCharts.createChart(el, {
    layout: { background: { color: "transparent" }, textColor: "#8b98a5" },
    grid: { vertLines: { visible: false }, horzLines: { color: "#1a2129" } },
    rightPriceScale: { borderColor: "#26303b" }, timeScale: { borderColor: "#26303b" }, autoSize: true,
    localization: { priceFormatter: (v) => kr ? Math.round(v).toLocaleString() : v.toFixed(2) },
  });
  const c = chart.addCandlestickSeries({ upColor: "#f04452", downColor: "#3182f6", borderVisible: false, wickUpColor: "#f04452", wickDownColor: "#3182f6" });
  c.setData(data.candles);
  const mas = detail ? [["sma20", "#ffd34d"], ["sma60", "#3fe07a"], ["sma120", "#c084fc"]] : [["sma20", "#ffd34d"]];
  mas.forEach(([k, col]) => chart.addLineSeries({ color: col, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(data[k]));
  const lastPx = data.candles.at(-1)?.close;
  const line = (price, color, title, style = 2, width = 1) => sane(price, lastPx) && c.createPriceLine({ price, color, lineWidth: width, lineStyle: style, axisLabelVisible: true, title });
  if (detail) {
    (data.levels.support || []).slice(0, 2).forEach((p) => line(p, "#5a6b7d", "지지"));
    (data.levels.resistance || []).slice(0, 2).forEach((p) => line(p, "#7d6b5a", "저항"));
  }
  if (f) {
    const P = run.plan;
    const mid = P?.entry || (sane(f.entry_low, lastPx) && sane(f.entry_high, lastPx) ? (f.entry_low + f.entry_high) / 2 : lastPx);
    const lv = [P?.stop || mid * (1 - f.stop_loss_pct / 100), P?.take || mid * (1 + f.take_profit_pct / 100)].filter((v) => sane(v, lastPx));
    // 손절·익절선이 화면 밖으로 나가지 않게 세로 범위를 넓힌다
    c.applyOptions({ autoscaleInfoProvider: (orig) => {
      const r = orig();
      if (r && lv.length) { r.priceRange.minValue = Math.min(r.priceRange.minValue, ...lv) * 0.99; r.priceRange.maxValue = Math.max(r.priceRange.maxValue, ...lv) * 1.01; }
      return r;
    } });
    line(mid, "#e8edf3", "매수", 2, 2);
    line(P?.stop || mid * (1 - f.stop_loss_pct / 100), "#3182f6", `손절 -${f.stop_loss_pct}%`, 0, 2);
    line(P?.take || mid * (1 + f.take_profit_pct / 100), "#f04452", `익절 +${f.take_profit_pct}%`, 0, 2);
    if (detail && f.scenarios?.bull) line(f.scenarios.bull.price, "#f08a5e", "강세", 1);
    if (detail && f.scenarios?.bear) line(f.scenarios.bear.price, "#5a9bff", "약세", 1);
  }
  chart.timeScale().fitContent();
  RES.chart = chart;
}

function planBlock(run) {
  const p = run.plan, cl = run.checklist;
  if (!p) return "";
  const m = p.currency === "USD" ? "US" : "KR";
  const steps = [
    ["1", "매수", `${fmtPx(p.entry_low, m)} ~ ${fmtPx(p.entry_high, m)} 사이에서 ${p.qty.toLocaleString()}주 (${fmtPx(p.amount, m)})`, ""],
    ["2", "손절", `${fmtPx(p.stop, m)} 이하로 내려가면 전량 매도 (-${p.stop_pct}%, 최대 손실 ${fmtPx(p.max_loss, m)})`, "down"],
    ["3", "익절", `${fmtPx(p.take, m)} 이상 오르면 전량 매도 (+${p.take_pct}%, 예상 이익 ${fmtPx(p.max_gain, m)})`, "up"],
    ["4", "기한", `${p.horizon_days}거래일 안에 둘 다 닿지 않으면 다시 회의해서 판단`, ""],
  ];
  const ok = cl ? cl.rules.filter((r) => r.ok).length : 0;
  return `<div class="res-grid">
    <div class="panel plan-card"><h3>실행 계획 <small>이대로 사고, 이 가격에서 판다</small></h3>
      ${steps.map(([n, k, v, c]) => `<div class="step ${c}"><span class="num">${n}</span><b>${k}</b><span>${esc(v)}</span></div>`).join("")}
      <div class="gauge">${(() => {
        const lo = p.stop * 0.98, hi = p.take * 1.02, pos = (v) => ((v - lo) / (hi - lo) * 100).toFixed(1);
        return `<div class="g-bar"><i class="g-loss" style="left:${pos(p.stop)}%;width:${pos(p.entry) - pos(p.stop)}%"></i><i class="g-gain" style="left:${pos(p.entry)}%;width:${pos(p.take) - pos(p.entry)}%"></i></div>
          <span class="g-m" style="left:${pos(p.stop)}%">손절 ${fmtPx(p.stop, m)}</span><span class="g-m mid" style="left:${pos(p.entry)}%">매수 ${fmtPx(p.entry, m)}</span><span class="g-m" style="left:${pos(p.take)}%">익절 ${fmtPx(p.take, m)}</span>`;
      })()}</div>
      <p class="note">수량은 운용 한도 × 종목당 최대 비중 × (절반 켈리·변동성 목표 중 작은 값)으로 정했습니다.</p></div>
    <div class="panel"><h3>매수 체크리스트 <small>${cl ? `${ok}/${cl.rules.length} 통과 · ${cl.passed ? "<b class='up'>매수 가능</b>" : "<b class='down'>매수 보류</b>"}` : ""} · 뇌동매매 방지</small></h3>
      ${cl ? `<table class="cl"><tbody>${cl.rules.map((r) => `<tr><td>${r.ok ? "<span class='chipst ok'>통과</span>" : "<span class='chipst bad'>미통과</span>"}</td>
        <td><b>${esc(r.name)}</b><div class="note">${esc(r.ref)}</div></td><td class="r">${esc(r.value)}</td><td class="note">${esc(r.need)}</td></tr>`).join("")}</tbody></table>` : ""}
    </div></div>`;
}

// 초보자용: 맨 위에 '한 줄 결론 + 얼마에 사고팔지 + 왜'만, 나머지는 '자세히 보기' 안으로
const PLAIN = { Buy: ["사도 좋아요", "근거가 충분해 계획대로 삽니다"], Overweight: ["조금 사 볼 만해요", "일부만 사고 지켜봅니다"],
  Hold: ["지금은 기다려요", "새로 사지도 팔지도 않습니다"], Underweight: ["줄이는 게 좋아요", "가진 것을 일부 팝니다"], Sell: ["팔아요", "위험이 커서 정리합니다"] };
function simplify(box, run, f, last, m) {
  const P = run.plan, cl = run.checklist;
  const ok = cl?.rules ? cl.rules.filter((r) => r.ok).length : 0, total = cl?.rules?.length || 0;
  const [title, sub] = PLAIN[f.rating] || ["판단 보류", ""];
  const color = R_COLOR[f.rating] || "#8b98a5";
  const easy = document.createElement("div");
  easy.className = "panel easy";
  easy.style.setProperty("--c", color);
  easy.innerHTML = `<div class="easy-top">
      <div class="easy-verdict"><span class="note">${esc(run.name || run.symbol)} · AI 회의 결론</span><b>${title}</b><span>${sub}</span>
        <div class="easy-conf"><i style="width:${Math.round((f.confidence || 0) * 100)}%"></i></div><span class="note">AI 확신 ${Math.round((f.confidence || 0) * 100)}%</span></div>
      <p class="easy-head">${esc(f.headline || "")}</p></div>
    ${P ? `<div class="easy-tiles">
      <div><span>이 가격에 사요</span><b>${fmtPx(P.entry, m)}</b><em>${P.qty.toLocaleString()}주 · ${fmtPx(P.amount, m)}</em></div>
      <div class="down"><span>여기까지 내리면 팔아요 (손절)</span><b>${fmtPx(P.stop, m)}</b><em>최대 손실 ${fmtPx(P.max_loss, m)} (-${P.stop_pct}%)</em></div>
      <div class="up"><span>여기까지 오르면 팔아요 (익절)</span><b>${fmtPx(P.take, m)}</b><em>예상 이익 ${fmtPx(P.max_gain, m)} (+${P.take_pct}%)</em></div>
      <div class="${cl?.passed ? "ok" : "bad"}"><span>안전 점검</span><b>${total ? `${ok} / ${total} 통과` : "-"}</b><em>${cl?.passed ? "모두 통과해야 실제로 삽니다" : "하나라도 못 넘으면 사지 않아요"}</em></div>
    </div>` : ""}
    <div class="easy-why"><div><h4>왜 이렇게 판단했나요?</h4><ol>${list((f.reasons || f.summary || []).slice(0, 3))}</ol></div>
      ${(f.worry || []).length ? `<div class="worry"><h4>조심할 점</h4><ul>${list(f.worry.slice(0, 2))}</ul></div>` : ""}</div>`;
  box.prepend(easy);
  // 회의 결과 머리말과 차트·계획 아래 내용은 접어 둔다
  const planGrid = box.querySelector(".plan-card")?.closest(".res-grid");
  const head = box.querySelector(".res-head");
  const det = document.createElement("details");
  det.className = "panel more-detail";
  det.innerHTML = `<summary><i data-lucide="book-open-text"></i> 전문가용 자세한 분석 보기 <small>토론 점수 · 시나리오 · 밸류에이션 · 투자 논리 · 반대 논리 · 팩트체크 · 토론 기록</small></summary>`;
  const rest = [];
  let after = !planGrid;
  for (const el of [...box.children]) {
    if (el === easy || el === head) continue;
    if (after && el.tagName !== "P") rest.push(el);
    if (el === planGrid) after = true;
  }
  if (head) det.appendChild(head);
  rest.forEach((el) => det.appendChild(el));
  (planGrid || box.lastElementChild).after(det);
  icons();
}

function list(arr) { return (arr || []).map((x) => `<li>${esc(x)}</li>`).join(""); }

window.renderResult = (run) => {
  const box = $("#result");
  if (!run) { box.innerHTML = ""; return; }
  const f = run.final, ck = run.check, facts = run.facts || {};
  const m = run.market;
  const last = facts.close;
  if (!f && !ck) {
    box.innerHTML = run.status === "running"
      ? `<div class="panel"><h3>분석 결과</h3><p class="note">회의가 끝나면 이 자리에 차트·시나리오·밸류에이션·투자 논문이 정리됩니다. 지금 단계: ${esc(run.stage)}</p></div>`
      : run.status === "error" ? `<div class="panel"><h3>분석 결과</h3><p class="down">${esc(run.error)}</p></div>` : "";
    return;
  }
  const key = run.id + ":" + (f ? "f" : "c");
  if (RES.chartFor === key) return; // 같은 결과면 다시 그리지 않는다
  RES.chartFor = key;
  const color = f ? R_COLOR[f.rating] : "#8b98a5";
  const entryOk = f && sane(f.entry_low, last) && sane(f.entry_high, last);
  const P = run.plan;
  const mid = entryOk ? (f.entry_low + f.entry_high) / 2 : last;
  const votes = f?.risk_votes || {};
  const bull = f?.bull_score ?? 50;
  box.innerHTML = `
  <div class="res-head panel">
    <div class="res-rating" style="--c:${color}">
      <div class="k">최종 의견 · ${esc(run.name || run.symbol)} <small>${esc(run.trade_date)}</small></div>
      <div class="big">${f ? R_KO[f.rating] : "판정 중"}<small>${f ? esc(f.rating) : ""}</small></div>
      ${f ? `<div class="conf"><span style="width:${Math.round(f.confidence * 100)}%;background:${color}"></span></div>
      <div class="note">확신도 ${Math.round(f.confidence * 100)}% · 보유 기간 ${f.horizon_days}거래일</div>` : ""}
    </div>
    <div class="res-sum">
      <div class="headline">${esc(f?.headline || ck?.summary || "")}</div>
      <ol>${list(f?.summary)}</ol>
      ${f ? `<div class="plan">
        <div><span class="k">현재가</span><b>${fmtPx(last, m)}</b></div>
        <div><span class="k">진입 가격대</span><b>${entryOk ? `${fmtPx(f.entry_low, m)} ~ ${fmtPx(f.entry_high, m)}` : "확인 필요"}</b></div>
        <div><span class="k">손절가</span><b class="down">${fmtPx(P ? P.stop : mid * (1 - f.stop_loss_pct / 100), m)} (-${f.stop_loss_pct}%)</b></div>
        <div><span class="k">익절가</span><b class="up">${fmtPx(P ? P.take : mid * (1 + f.take_profit_pct / 100), m)} (+${f.take_profit_pct}%)</b></div>
        <div><span class="k">손익비</span><b>${(f.take_profit_pct / Math.max(f.stop_loss_pct, 0.1)).toFixed(1)} : 1</b></div>
      </div>` : ""}
    </div>
    <div class="res-votes">
      <div class="k">강세 vs 약세 토론</div>
      <div class="tug"><span class="bullside" style="width:${bull}%">강세 ${Math.round(bull)}</span><span class="bearside">약세 ${Math.round(100 - bull)}</span></div>
      <div class="k" style="margin-top:10px">리스크 위원회</div>
      <div class="votes">${[["aggr", "돌격·공격형"], ["neut", "저울·중립형"], ["cons", "방패·보수형"]].map(([k, n]) =>
        `<div><span>${n}</span><b style="color:${VOTE_COLOR[votes[k]] || "#8b98a5"}">${esc(votes[k] || "-")}</b></div>`).join("")}</div>
    </div>
  </div>

  <div class="res-grid">
    <div class="panel"><div class="rank-bar"><h3 style="margin:0">가격 차트</h3>
      <div class="seg" id="res-chart-mode"><button data-v="simple" class="on">간단히</button><button data-v="detail">자세히</button></div></div>
      <div id="res-chart" class="res-chart"></div>
      <div class="chart-legend"><span><i style="background:#e8edf3"></i>매수 가격</span><span><i style="background:#3182f6"></i>손절: 여기까지 내리면 판다</span><span><i style="background:#f04452"></i>익절: 여기까지 오르면 판다</span><span><i style="background:#ffd34d"></i>20일 평균선</span></div>
      <div class="facts">${[["RSI", facts.rsi14], ["20일선 대비", facts.vs_sma20 != null ? facts.vs_sma20 + "%" : "-"], ["52주 고가 대비", facts.from_high != null ? facts.from_high + "%" : "-"],
        ["연 변동성", facts.vol_annual != null ? facts.vol_annual + "%" : "-"], ["거래량(5/20일)", facts.vol_ratio != null ? facts.vol_ratio + "배" : "-"]]
        .map(([k, v]) => `<div><span>${k}</span><b>${v ?? "-"}</b></div>`).join("")}</div></div>
    <div class="panel"><h3>분야별 점수 <small>검증관 정리 · 0~100</small></h3>${ck?.scores ? radar(ck.scores) : "<p class='note'>점수 없음</p>"}</div>
  </div>

  ${planBlock(run)}

  ${f ? `<div class="panel"><h3>시나리오와 기대값</h3>${scenarioBlock(f, last, m)}</div>` : ""}

  <div class="panel"><h3>밸류에이션 비교 <small>현재 vs 5년 평균 vs 경쟁사 평균</small></h3>${valuationBlock(ck?.valuation)}</div>

  ${f ? `<div class="res-grid">
    <div class="panel"><h3>투자 논문 (Investment Thesis)</h3><p class="thesis">${esc(f.thesis)}</p><h4>핵심 근거 3가지</h4><ol>${list(f.reasons)}</ol></div>
    <div class="panel"><h3>숏 논거와 반박</h3>${(f.short_args || []).map((s, i) => `<div class="short"><div><b class="down">#${i + 1} 반대 논리</b> ${esc(s.claim)}</div><div><b class="up">반박</b> ${esc(s.rebuttal)}</div></div>`).join("")}</div>
  </div>
  <div class="tww">
    <div class="panel"><h3 class="up">Trust · 핵심 확신</h3><ul>${list(f.trust)}</ul></div>
    <div class="panel"><h3 class="down">Worry · 주요 리스크</h3><ul>${list(f.worry)}</ul></div>
    <div class="panel"><h3 style="color:#ffd34d">Watch · 지켜볼 지표</h3><ul>${list(f.watch)}</ul></div>
  </div>` : ""}

  ${ck ? `<div class="res-grid">
    <div class="panel"><h3>팩트체크 <small>분석가 주장을 원자료와 대조</small></h3>
      <table class="fc"><thead><tr><th>판정</th><th>주장</th><th>근거</th></tr></thead><tbody>
      ${(ck.fact_checks || []).map((x) => `<tr><td><span class="chipst ${x.status === "확인됨" ? "ok" : x.status === "틀림" ? "bad" : "unk"}">${esc(x.status)}</span></td>
        <td>${esc(x.claim)}</td><td>${esc(x.detail)} <span class="note">${esc(x.source)}</span></td></tr>`).join("")}</tbody></table></div>
    <div class="panel"><h3>투자 전에 꼭 알아야 할 질문</h3>
      ${(ck.key_questions || []).map((q, i) => `<details ${i === 0 ? "open" : ""}><summary>Q${i + 1}. ${esc(q.question)}</summary><p>${esc(q.answer)}</p><p class="note">${esc(q.source)}</p></details>`).join("")}</div>
  </div>` : ""}

  ${run.debate?.length ? `<div class="panel"><h3>토론 기록</h3><div class="debate">${run.debate.map((d) =>
    `<div class="dmsg ${d.role}"><b>${d.role === "bull" ? "불리 · 강세" : "베어리 · 약세"} ${d.round}라운드</b>${mdLite(d.text)}</div>`).join("")}</div></div>` : ""}
  <p class="note">AI 회의 결과는 공개 자료와 모델 추론으로 만든 추정입니다. 투자 권유가 아니며 수익을 보장하지 않습니다. 중요한 숫자는 출처 원문을 직접 확인하세요.</p>`;
  if (f) simplify(box, run, f, last, m);
  RES.detail = false;
  drawChart(run, f, false);
  $("#res-chart-mode").onclick = (e) => { const b = e.target.closest("button"); if (b) drawChart(run, f, b.dataset.v === "detail"); };
};
