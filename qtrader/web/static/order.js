"use strict";
// 종목 화면의 주문창: 구매·판매, 지정가·시장가, 호가 단위 ±, 수량 비율 버튼, 확인 시트. 실전 계좌면 PIN 을 묻는다.

const tk = { side: "buy", type: "limit", how: "now", info: null, price: 0, qty: 0, sym: null };

// 국내 호가 단위 (2023 개편 기준)
function tickKR(p) {
  return p < 2000 ? 1 : p < 5000 ? 5 : p < 20000 ? 10 : p < 50000 ? 50 : p < 200000 ? 100 : p < 500000 ? 500 : 1000;
}
const tkKR = () => tk.info?.market === "KR";
const tkStep = (p) => tkKR() ? tickKR(p) : 0.01;
const tkRound = (p) => tkKR() ? Math.round(p / tickKR(p)) * tickKR(p) : Math.round(p * 100) / 100;
const tkMoney = (v) => isUS(tk.info?.market) ? usdWithKrw(v) : money(v, tk.info?.market || "KR");

function ticketShell() {
  const box = $("#ticket");
  box.innerHTML = `
    <div class="tk-side seg"><button data-side="buy" class="buy on">구매</button><button data-side="sell" class="sell">판매</button></div>
    <div class="tk-row"><span>주문 방법</span><div class="seg tk-how"><button data-how="now" class="on">바로</button><button data-how="cond" title="정한 가격에 닿으면 주문">조건</button><button data-how="split" title="여러 번에 나눠서">나눠서</button></div></div>
    <div class="tk-row"><span>주문 유형</span><div class="seg tk-type"><button data-type="limit" class="on">지정가</button><button data-type="market">시장가</button></div></div>
    <div class="tk-extra hidden" id="tk-cond-box"><label>이 가격에 닿으면 <div class="tk-pct"><input id="tk-trig" inputmode="decimal"></div></label>
      <label class="chk-inline" id="tk-oto-row"><input type="checkbox" id="tk-oto" checked> 사지면 바로 손절도 걸기 <div class="tk-pct"><input id="tk-oto-pct" value="5" inputmode="decimal">%</div></label>
      <p class="note" id="tk-cond-note"></p></div>
    <div class="tk-extra hidden" id="tk-split-box"><div class="tk-guard-row"><label>몇 번에 <select id="tk-parts"><option>2</option><option selected>3</option><option>4</option></select></label>
      <label>간격 <div class="tk-pct"><input id="tk-step" inputmode="decimal" value="3">%</div></label></div><p class="note" id="tk-split-note"></p></div>
    <label class="tk-field"><span>가격</span><div class="tk-input"><button type="button" data-step="price:-1">−</button>
      <input id="tk-price" inputmode="decimal" autocomplete="off"><button type="button" data-step="price:1">＋</button></div></label>
    <label class="tk-field"><span>수량</span><div class="tk-input"><button type="button" data-step="qty:-1">−</button>
      <input id="tk-qty" inputmode="numeric" autocomplete="off" placeholder="0"><button type="button" data-step="qty:1">＋</button></div></label>
    <div class="tk-pcts">${[10, 25, 50, 100].map((p) => `<button type="button" data-pct="${p}">${p === 100 ? "최대" : p + "%"}</button>`).join("")}</div>
    <div class="tk-guard" id="tk-guard">
      <label class="check"><input type="checkbox" id="tk-guard-on" checked> 손절·익절도 정하기 <small class="note">닿으면 자동으로 팔아요</small></label>
      <div class="tk-guard-row"><label>손절 <span class="down" id="tk-stop-px"></span><div class="tk-pct"><input id="tk-stop" inputmode="decimal">%</div></label>
        <label>익절 <span class="up" id="tk-take-px"></span><div class="tk-pct"><input id="tk-take" inputmode="decimal">%</div></label></div>
    </div>
    <dl class="tk-sum">
      <div><dt id="tk-avail-k">주문 가능</dt><dd id="tk-avail">-</dd></div>
      <div><dt>예상 수수료${tkKR() ? "·세금" : ""}</dt><dd id="tk-fee">-</dd></div>
      <div class="tot"><dt>총 주문 금액</dt><dd id="tk-total">0</dd></div>
    </dl>
    <button class="tk-go buy" id="tk-go">구매하기</button>
    <p class="note tk-note" id="tk-note"></p>`;
  box.querySelector(".tk-side").onclick = (e) => { const b = e.target.closest("[data-side]"); if (b) { tk.side = b.dataset.side; ticketDraw(); } };
  box.querySelector(".tk-how").onclick = (e) => { const b = e.target.closest("[data-how]"); if (b) { tk.how = b.dataset.how; ticketDraw(); } };
  ["#tk-trig", "#tk-parts", "#tk-step"].forEach((s) => box.querySelector(s).addEventListener("input", () => ticketDraw(false)));
  box.querySelector(".tk-type").onclick = (e) => {
    const b = e.target.closest("[data-type]");
    if (!b || (b.dataset.type === "market" && !tk.info?.market_order)) return toast("미국 주식은 지정가로만 주문할 수 있어요", "info");
    tk.type = b.dataset.type; ticketDraw();
  };
  box.addEventListener("click", (e) => {
    const s = e.target.closest("[data-step]");
    if (s) {
      const [k, d] = s.dataset.step.split(":");
      if (k === "price") tk.price = Math.max(0, tkRound((tk.price || tk.info?.price || 0) + Number(d) * tkStep(tk.price || tk.info?.price || 1)));
      else tk.qty = Math.max(0, (tk.qty || 0) + Number(d));
      ticketDraw();
    }
    const p = e.target.closest("[data-pct]");
    if (p) { tk.qty = Math.floor(maxQty() * Number(p.dataset.pct) / 100); ticketDraw(); }
  });
  $("#tk-price").oninput = (e) => { tk.price = Number(e.target.value.replace(/,/g, "")) || 0; ticketDraw(false); };
  $("#tk-qty").oninput = (e) => { tk.qty = Math.floor(Number(e.target.value.replace(/,/g, "")) || 0); ticketDraw(false); };
  $("#tk-go").onclick = confirmOrder;
  const H = { auto: [7, 15], long: [7, 15], swing: [5, 10], short: [2.5, 5] }[state.config?.ai?.holding || "auto"] || [7, 15];
  $("#tk-stop").value = H[0]; $("#tk-take").value = H[1];
  ["#tk-stop", "#tk-take", "#tk-guard-on"].forEach((s) => $(s).addEventListener("input", () => ticketDraw(false)));
}

function unitPrice() { return tk.type === "market" ? tk.info?.price || 0 : tk.price; }
function maxQty() {
  const i = tk.info, p = unitPrice();
  if (!i) return 0;
  if (tk.side === "sell") return i.sellable || 0;
  return p > 0 ? Math.floor(Math.min(i.cash, i.max_order) / (p * (1 + i.fee))) : 0;
}

function ticketDraw(syncInputs = true) {
  const i = tk.info;
  if (!i) return;
  const buy = tk.side === "buy", p = unitPrice(), gross = p * tk.qty;
  const fee = gross * i.fee + (buy ? 0 : gross * i.sell_tax);
  $$("#ticket .tk-side button").forEach((b) => b.classList.toggle("on", b.dataset.side === tk.side));
  $$("#ticket .tk-type button").forEach((b) => { b.classList.toggle("on", b.dataset.type === tk.type); b.disabled = b.dataset.type === "market" && !i.market_order; });
  $("#tk-price").disabled = tk.type === "market";
  if (syncInputs) {
    $("#tk-price").value = tk.type === "market" ? "시장가" : tkKR() ? Math.round(tk.price).toLocaleString() : tk.price.toFixed(2);
    $("#tk-qty").value = tk.qty ? tk.qty.toLocaleString() : "";
  }
  $("#tk-avail-k").textContent = buy ? "주문 가능 금액" : "판매 가능 수량";
  $("#tk-avail").textContent = buy ? tkMoney(i.cash) : `${(i.sellable || 0).toLocaleString()}주` + (i.avg_price ? ` · 평균 ${tkMoney(i.avg_price)}` : "");
  $("#tk-fee").textContent = tkMoney(fee);
  $("#tk-total").textContent = tkMoney(buy ? gross + fee : gross - fee);
  const go = $("#tk-go");
  go.className = "tk-go " + (buy ? "buy" : "sell");
  const liveTk = LIVE.has(state.status?.mode);
  go.textContent = (liveTk ? "실전 · " : "가상 · ") + (tk.how === "cond" ? (buy ? "조건 구매 걸기" : "조건 판매 걸기") : tk.how === "split" ? (buy ? "나눠서 사기" : "나눠서 팔기") : buy ? "구매하기" : "판매하기");
  go.classList.toggle("is-live", liveTk);
  $("#tk-guard").classList.toggle("hidden", !buy || tk.how !== "now");
  $$("#ticket .tk-how button").forEach((b) => b.classList.toggle("on", b.dataset.how === tk.how));
  $("#tk-cond-box").classList.toggle("hidden", tk.how !== "cond");
  $("#tk-split-box").classList.toggle("hidden", tk.how !== "split");
  if (tk.how === "cond") {
    if (!$("#tk-trig").value && i.price) $("#tk-trig").value = tkRound(i.price * (buy ? 0.97 : 1.03));
    const tg = Number($("#tk-trig").value.replace(/,/g, "")) || 0;
    $("#tk-oto-row").classList.toggle("hidden", !buy);
    const oto = buy && $("#tk-oto").checked ? tkRound(tg * (1 - (Number($("#tk-oto-pct").value) || 5) / 100)) : 0;
    $("#tk-cond-note").textContent = tg ? `지금 ${tkMoney(i.price)} → ${tkMoney(tg)}에 ${buy ? (tg < i.price ? "내려오면 사요" : "닿으면 사요") : tg < i.price ? "내려오면 팔아요 (손절)" : "오르면 팔아요"} (${pct(tg / i.price - 1)}). 30일 동안 지켜 봐요.`
      + (oto ? ` 사지면 ${tkMoney(oto)} 아래로 내릴 때 파는 손절이 이어서 걸려요.` : "") : "";
  }
  if (tk.how === "split") {
    const n = Number($("#tk-parts").value), step = Number($("#tk-step").value) || 0;
    const ps = Array.from({ length: n }, (_, k) => tkRound((p || i.price) * (1 + (buy ? -1 : 1) * step / 100 * k)));
    $("#tk-split-note").textContent = tk.qty >= n ? `${ps.map((x, k) => `${k + 1}번 ${tkMoney(x)}`).join(" → ")} · 첫 번째는 바로, 나머지는 가격에 닿으면 나가요` : `${n}번에 나누려면 ${n}주 이상이어야 해요`;
  }
  const gOn = buy && $("#tk-guard-on").checked;
  $("#tk-guard .tk-guard-row").classList.toggle("off", !gOn);
  tk.stop = gOn && p ? tkRound(p * (1 - Number($("#tk-stop").value || 0) / 100)) : 0;
  tk.take = gOn && p ? tkRound(p * (1 + Number($("#tk-take").value || 0) / 100)) : 0;
  $("#tk-stop-px").textContent = tk.stop ? tkMoney(tk.stop) : "";
  $("#tk-take-px").textContent = tk.take ? tkMoney(tk.take) : "";
  const over = tk.qty > maxQty();
  go.disabled = !tk.qty || !p || over;
  $("#tk-note").innerHTML = over ? `<span class="down">${buy ? "주문 가능 금액(또는 1회 주문 한도)을 넘었어요" : "판매 가능 수량을 넘었어요"}</span>`
    : `${i.live ? "<b class='up'>실전 계좌</b> · 실제 돈으로 주문됩니다" : "가상 계좌 · 내 PC 안에서만 체결됩니다"}${i.market === "US" && buy ? " · 달러로만 사요 (모자라면 환전 우대로 먼저 바꿔 주세요)" : ""}${i.kill_switch ? " · 긴급 정지는 자동매매만 막고 직접 주문은 막지 않아요" : ""}`;
}

async function ticketLoad(sym) {
  tk.sym = sym;
  try {
    const i = await api("/api/order/info/" + encodeURIComponent(sym));
    if (tk.sym !== sym) return;
    const first = !tk.info || tk.info.symbol !== sym;
    tk.info = i;
    if (first) { tk.price = i.price ? tkRound(i.price) : 0; tk.qty = 0; tk.type = "limit"; ticketShell(); }
    if (!i.market_order && tk.type === "market") tk.type = "limit";
    ticketDraw(first);
  } catch (e) { $("#ticket").innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}
window.ticketLoad = ticketLoad;
// 호가를 누르면 그 가격으로
window.ticketSetPrice = (p) => { if (!tk.info) return; tk.type = "limit"; tk.price = tkRound(p); ticketDraw(); $("#tk-qty").focus(); };

async function sendSpecial() {
  const i = tk.info, buy = tk.side === "buy", p = unitPrice();
  try {
    if (tk.how === "cond") {
      const trig = Number($("#tk-trig").value.replace(/,/g, ""));
      const oto = buy && $("#tk-oto").checked ? tkRound(trig * (1 - (Number($("#tk-oto-pct").value) || 5) / 100)) : 0;
      await api("/api/conditional", { method: "POST", body: { symbol: i.symbol, side: tk.side, qty: tk.qty, trigger: trig, price: tk.type === "market" && !oto ? 0 : trig, then_stop: oto } });
      toast(`조건 주문을 걸었어요. ${tkMoney(trig)}에 닿으면 ${buy ? "사요" : "팔아요"}`, "crosshair");
    } else {
      const r = await api("/api/order/split", { method: "POST", body: { symbol: i.symbol, side: tk.side, qty: tk.qty, parts: Number($("#tk-parts").value), step_pct: Number($("#tk-step").value) || 3, price: tk.type === "market" ? 0 : p } });
      toast(`첫 ${r.first.status === "filled" ? "주문 체결" : "주문 접수"} · 나머지 ${r.conditions.length}번은 조건 주문으로 걸었어요`, "layers");
    }
    tk.qty = 0; ticketLoad(i.symbol);
  } catch (err) { toast(err.message, "alert-triangle"); }
}

function confirmOrder() {
  if (tk.how !== "now") {
    const msg = tk.how === "cond" ? `${tk.qty}주 조건 ${tk.side === "buy" ? "구매" : "판매"}를 걸까요?\n${$("#tk-cond-note").textContent}` : `${tk.qty}주를 나눠서 ${tk.side === "buy" ? "살까요" : "팔까요"}?\n${$("#tk-split-note").textContent}`;
    if (confirm(msg)) sendSpecial();
    return;
  }
  const i = tk.info, buy = tk.side === "buy", p = unitPrice(), gross = p * tk.qty;
  const name = st.sum?.name || i.symbol;
  modal(`<div class="tk-confirm ${buy ? "buy" : "sell"}">
    <div class="tk-c-head">${logo(i.symbol, name, 40)}<div><b>${esc(name)}</b><span class="note">${esc(i.symbol)}</span></div></div>
    <h3>${tk.qty.toLocaleString()}주를 ${tk.type === "market" ? "시장가로" : tkMoney(p) + "에"} ${buy ? "구매" : "판매"}할까요?</h3>
    <dl class="tk-sum"><div><dt>주문 유형</dt><dd>${tk.type === "market" ? "시장가" : "지정가"}</dd></div>
      <div><dt>${tk.type === "market" ? "예상 가격" : "주문 가격"}</dt><dd>${tkMoney(p)}</dd></div>
      <div><dt>수량</dt><dd>${tk.qty.toLocaleString()}주</dd></div>
      <div class="tot"><dt>총 ${buy ? "주문" : "판매"} 금액</dt><dd>${tkMoney(gross)}</dd></div>
      <div><dt>계좌</dt><dd>${i.live ? "<b class='up'>실전</b> " : "가상 "}${esc(MODE_NAME[i.mode] || i.mode)}</dd></div>
      ${buy ? `<div><dt>손절 · 익절</dt><dd>${tk.stop ? `<span class="down">${tkMoney(tk.stop)}</span> · <span class="up">${tkMoney(tk.take)}</span>` : "정하지 않음 (자동으로 팔지 않아요)"}</dd></div>` : ""}</dl>
    ${i.live ? `<p class="note">실제 돈이 움직입니다. 확인을 누르면 PIN 을 물을 수 있어요.</p>` : ""}
    <div class="tk-c-btns"><button id="tk-cancel">취소</button><button class="tk-go ${buy ? "buy" : "sell"}" id="tk-ok">${buy ? "구매" : "판매"} 확인</button></div></div>`);
  $("#tk-cancel").onclick = () => $("#modal").classList.add("hidden");
  $("#tk-ok").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "주문 보내는 중...";
    try {
      const r = await api("/api/order", { method: "POST", body: { symbol: i.symbol, side: tk.side, qty: tk.qty, price: tk.type === "market" ? 0 : p, stop: buy ? tk.stop : 0, take: buy ? tk.take : 0 } });
      $("#modal-body").innerHTML = `<div class="tk-done"><div class="tk-check">✓</div><h3>${r.status === "filled" ? (buy ? "구매 완료" : "판매 완료") : "주문 접수"}</h3>
        <p>${esc(name)} ${tk.qty.toLocaleString()}주 · ${tkMoney(r.price)}</p>
        <p class="note">${r.status === "filled" ? "체결됐어요. 내 계좌에서 확인할 수 있어요." : "증권사에 접수됐어요. 체결되면 알림으로 알려 드릴게요."}</p>
        <button class="primary" id="tk-close">확인</button></div>`;
      window.anime && anime({ targets: ".tk-check", scale: [0.2, 1], rotate: [-30, 0], duration: 520, easing: "easeOutBack" });
      $("#tk-close").onclick = () => $("#modal").classList.add("hidden");
      tk.qty = 0;
      ticketLoad(i.symbol);
    } catch (err) {
      e.target.disabled = false; e.target.textContent = buy ? "구매 확인" : "판매 확인";
      toast(err.message, "alert-triangle");
    }
  };
}
