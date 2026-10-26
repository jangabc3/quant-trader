"use strict";
// 뉴스 탭: RSS 피드 (인베스팅닷컴 주식시장 뉴스 등) 제목·시간·링크

const nw = { items: [], src: "전체", loaded: 0 };

function renderNews() {
  const q = $("#news-q").value.trim();
  const items = nw.items.filter((n) => (nw.src === "전체" || n.source === nw.src) && (!q || n.title.includes(q)));
  $("#news-list").innerHTML = window.newsItems(items) || `<p class="note">뉴스가 없습니다.</p>`;
}

async function loadNewsTab(force = false) {
  if (!force && Date.now() - nw.loaded < 120000) return renderNews();
  $("#news-time").textContent = "불러오는 중...";
  try {
    const d = await api("/api/news");
    nw.items = d.items;
    nw.loaded = Date.now();
    const srcs = ["전체", ...new Set(d.items.map((n) => n.source))];
    $("#news-src").innerHTML = srcs.map((s) => `<button data-v="${esc(s)}" class="${s === nw.src ? "on" : ""}">${esc(s)}</button>`).join("");
    $("#news-time").textContent = new Date().toLocaleTimeString("ko-KR") + " 기준" + (d.errors.length ? ` · 실패 ${d.errors.length}개: ${d.errors.join(", ")}` : "");
    renderNews();
  } catch (e) { $("#news-time").textContent = e.message; }
}

$("#news-src").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  nw.src = b.dataset.v;
  $$("#news-src button").forEach((x) => x.classList.toggle("on", x === b));
  renderNews();
});
$("#news-q").addEventListener("input", renderNews);
$("#news-refresh").onclick = () => loadNewsTab(true);
document.addEventListener("tab", (e) => { if (e.detail === "news") loadNewsTab(); });
setInterval(() => { if (state.tab === "news" && !document.hidden) loadNewsTab(true); }, 300000);
