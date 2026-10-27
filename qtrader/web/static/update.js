"use strict";
// 자동 업데이트: 하루에 몇 번 새 버전을 확인해 위쪽에 알리고, 설정에서 바뀐 점을 보고 한 번에 업데이트
async function updCheck(force) {
  const body = $("#upd-body");
  try {
    const u = await api("/api/update" + (force ? "?force=1" : ""));
    $("#upd-ver").textContent = `지금 v${u.current}`;
    if ($("#upd-repo") && document.activeElement !== $("#upd-repo")) $("#upd-repo").value = state.config?.update?.repo || "";
    if (!u.repo) { body.innerHTML = "업데이트를 받을 GitHub 저장소를 적으면, 새 버전이 나왔을 때 알려 주고 한 번에 바꿔 줘요."; return u; }
    if (!u.available) { body.innerHTML = u.reason ? esc(u.reason) : `최신 버전이에요 (v${esc(u.latest || u.current)})`; return u; }
    body.innerHTML = `<b>새 버전 v${esc(u.latest)}</b> ${u.published ? `<span class="note">${esc(u.published.slice(0, 10))}</span>` : ""}
      <pre class="upd-notes">${esc(u.notes || "바뀐 점이 적혀 있지 않아요")}</pre>
      ${u.installed ? `<button class="primary" id="upd-go"><i data-lucide="download"></i> 지금 업데이트 (잠깐 꺼졌다 켜져요)</button>`
        : `<p class="note">개발 버전이에요. 프로젝트 폴더에서 git pull 로 받아 주세요.</p>`}`;
    $("#upd-go") && ($("#upd-go").onclick = updApply);
    icons();
    updBanner(u);
    return u;
  } catch (e) { body.textContent = e.message; }
}
function updBanner(u) {
  if (!u.available || $("#upd-banner")) return;
  try { if (localStorage.getItem("qtrader.upd.skip") === u.latest) return; } catch {}
  const b = document.createElement("div");
  b.id = "upd-banner";
  b.className = "upd-banner";
  b.innerHTML = `<i data-lucide="sparkles"></i><span>새 버전 <b>v${esc(u.latest)}</b> 이 나왔어요</span><button data-a="see">바뀐 점 보기</button><button data-a="later">나중에</button>`;
  document.body.appendChild(b);
  b.onclick = (e) => {
    const a = e.target.closest("button")?.dataset.a;
    if (a === "see") { openTab("cfg"); $("#upd-panel").scrollIntoView({ behavior: "smooth" }); }
    if (a === "later") { try { localStorage.setItem("qtrader.upd.skip", u.latest); } catch {} }
    if (a) b.remove();
  };
  icons();
}
async function updApply() {
  if (!(await ask("업데이트하면 프로그램이 잠깐 꺼졌다가 다시 켜져요. 키·기록·설정은 그대로 남아요.\n자동매매 중이라면 장이 열리지 않은 시간에 하는 게 좋아요. 지금 할까요?"))) return;
  const b = $("#upd-go");
  b.disabled = true;
  b.textContent = "내려받는 중...";
  try {
    const r = await api("/api/update/apply", { method: "POST" });
    $("#upd-body").innerHTML = `v${esc(r.latest)} 로 바꾸는 중이에요. 잠시 뒤 새 창이 열리면 이 창은 닫아도 돼요.`;
  } catch (e) { toast(e.message); b.disabled = false; b.textContent = "다시 시도"; }
}
$("#upd-check")?.addEventListener("click", async () => {
  const repo = $("#upd-repo").value.trim();
  if (repo !== (state.config?.update?.repo || "")) {
    if (repo && !/^[\w.-]+\/[\w.-]+$/.test(repo)) { toast("아이디/저장소 모양으로 적어 주세요"); return; }
    // 설정은 통째로 저장된다 (일부만 보내면 나머지가 기본값으로 돌아감)
    const cur = await api("/api/config");
    try { state.config = await api("/api/config", { method: "PUT", body: { ...cur, update: { ...(cur.update || {}), repo } } }); }
    catch (e) { toast(e.message); return; }
  }
  updCheck(true);
});
document.addEventListener("tab", (e) => { if (e.detail === "cfg") updCheck(false); });
setTimeout(() => updCheck(false), 4000);
setInterval(() => updCheck(false), 6 * 3600 * 1000);
