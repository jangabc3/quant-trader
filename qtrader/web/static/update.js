"use strict";
// 자동 업데이트: 하루에 몇 번 새 버전을 확인해 위쪽에 알리고, 설정에서 바뀐 점을 보고 한 번에 업데이트
// 패치노트: 새 기능 · 고친 것 · 빨라진 것으로 묶어서
const PN_ICON = { "새 기능": "sparkles", "고친 것": "wrench", "빨라진 것": "zap", "손본 것": "brush" };
function patchNotesHtml(changes, raw) {
  if (!changes?.length) return `<pre class="upd-notes">${esc(raw || "바뀐 점이 적혀 있지 않아요")}</pre>`;
  const groups = {};
  changes.forEach((c) => (groups[c.kind] = groups[c.kind] || []).push(c.text));
  return `<div class="pn">${Object.entries(groups).map(([k, xs]) => `<section><b class="pn-k"><i data-lucide="${PN_ICON[k] || "dot"}"></i>${esc(k)} <small>${xs.length}</small></b>
    <ul>${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></section>`).join("")}</div>`;
}
function recentHtml(r) {
  return `<div class="pn-recent"><b><i data-lucide="party-popper"></i> 이번 업데이트로 바뀐 것</b>${r.at ? ` <span class="note">${esc(r.at)} 설치</span>` : ""}
    ${patchNotesHtml(r.changes)}<button id="upd-seen">확인했어요</button></div>`;
}
async function updSeen() {
  try { await api("/api/update/seen", { method: "POST", body: {} }); } catch {}
  $$(".pn-recent, #upd-done-banner").forEach((x) => x.remove());
}
async function updCheck(force) {
  const body = $("#upd-body");
  try {
    const u = await api("/api/update" + (force ? "?force=1" : ""));
    const git = u.mode === "git";  // 내 PC 소스 폴더에서 다시 만드는 방식: 버전 번호 대신 커밋으로 비교
    $("#upd-ver").textContent = git ? `지금 v${u.current} · ${u.current_rev || ""}` : `지금 v${u.current}`;
    $("#upd-repo-row")?.classList.toggle("hidden", git);
    if ($("#upd-repo") && document.activeElement !== $("#upd-repo")) $("#upd-repo").value = state.config?.update?.repo || "";
    if (!u.repo) { body.innerHTML = "'QTrader 설치.cmd' 로 설치하면 새 버전이 나왔을 때 알려 주고, 버튼 하나로 바꿔 줘요."; return u; }
    const recent = u.recent ? recentHtml(u.recent) : "";
    if (u.recent) updDoneBanner(u.recent);
    if (!u.available) {
      body.innerHTML = recent + (u.reason ? esc(u.reason) : git ? "최신 버전이에요." : `최신 버전이에요 (v${esc(u.latest || u.current)})`);
      $("#upd-seen") && ($("#upd-seen").onclick = updSeen); icons();
      return u;
    }
    body.innerHTML = recent + (git ? `<b>새 버전이 있어요</b> <span class="note">바뀐 것 ${esc(u.behind)}개</span>`
      : `<b>새 버전 v${esc(u.latest)}</b> ${u.published ? `<span class="note">${esc(u.published.slice(0, 10))}</span>` : ""}`) + `
      ${patchNotesHtml(u.changes, u.notes)}
      ${u.installed ? `<button class="primary" id="upd-go"><i data-lucide="download"></i> 지금 업데이트 (${git ? "몇 분 동안 꺼졌다 켜져요" : "잠깐 꺼졌다 켜져요"})</button>`
        : `<p class="note">개발 버전이에요. 프로젝트 폴더에서 git pull 로 받아 주세요.</p>`}`;
    $("#upd-go") && ($("#upd-go").onclick = updApply);
    $("#upd-seen") && ($("#upd-seen").onclick = updSeen);
    icons();
    updBanner(u);
    return u;
  } catch (e) { body.textContent = e.message; }
}
// 업데이트 뒤 처음 켰을 때 한 번: 위쪽에 '업데이트를 마쳤어요 · 바뀐 것 보기'
function updDoneBanner(r) {
  if ($("#upd-done-banner")) return;
  const b = document.createElement("div");
  b.id = "upd-done-banner";
  b.className = "upd-banner";
  b.innerHTML = `<i data-lucide="party-popper"></i><span>업데이트를 마쳤어요 <b>(바뀐 것 ${r.changes.length}개)</b></span><button data-a="see">바뀐 것 보기</button><button data-a="ok">닫기</button>`;
  document.body.appendChild(b);
  b.onclick = (e) => {
    const a = e.target.closest("button")?.dataset.a;
    if (a === "see") { openTab("cfg"); $("#upd-panel").scrollIntoView({ behavior: "smooth" }); b.remove(); }
    if (a === "ok") updSeen();
  };
  icons();
}
function updBanner(u) {
  if (!u.available || $("#upd-banner")) return;
  try { if (localStorage.getItem("qtrader.upd.skip") === u.latest) return; } catch {}
  const b = document.createElement("div");
  b.id = "upd-banner";
  b.className = "upd-banner";
  b.innerHTML = `<i data-lucide="sparkles"></i><span>${u.mode === "git" ? `새 버전이 나왔어요 <b>(바뀐 것 ${esc(u.behind)}개)</b>` : `새 버전 <b>v${esc(u.latest)}</b> 이 나왔어요`}</span><button data-a="see">바뀐 것 보기</button><button data-a="later">나중에</button>`;
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
  if (!(await ask("업데이트하면 프로그램이 꺼지고, 최신 코드를 받아 다시 만든 뒤 다시 켜져요 (몇 분 걸려요). 키·기록·설정은 그대로 남아요.\n그동안 손절·익절 감시가 멈추니, 장이 열리지 않은 시간에 하는 게 좋아요. 지금 할까요?"))) return;
  const b = $("#upd-go");
  b.disabled = true;
  b.textContent = "내려받는 중...";
  try {
    const r = await api("/api/update/apply", { method: "POST" });
    $("#upd-body").innerHTML = r.mode === "git" ? "곧 프로그램이 꺼지고, 까만 창에서 새 버전을 만들어요. 끝나면 QTrader 가 다시 켜져요."
      : `v${esc(r.latest)} 로 바꾸는 중이에요. 잠시 뒤 새 창이 열리면 이 창은 닫아도 돼요.`;
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
