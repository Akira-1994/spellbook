(() => {
  const csrf = document.body.dataset.csrf;
  const state = { items: [], hasMore: false, loadingMore: false, listToken: 0, current: null, original: {}, status: "", issue: "", letter: "", dirty: false, pdfPage: 0, draftTimer: null };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const form = $("#spell-form");
  const editable = ["spell.name_zh", "spell.name_en", "entry.school", "entry.subschool", "entry.components", "entry.casting_time", "entry.range_text", "entry.target_text", "entry.area_text", "entry.effect_text", "entry.duration", "entry.saving_throw", "entry.spell_resistance", "entry.additional_costs", "entry.description_zh", "entry.description_en"];

  async function api(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, ...(options.headers || {}) },
    });
    const payload = response.headers.get("content-type")?.includes("json") ? await response.json() : null;
    if (!response.ok) throw new Error(payload?.detail || `操作失敗（${response.status}）`);
    return payload;
  }

  function toast(message, error = false) {
    const element = $("#toast");
    element.textContent = message;
    element.className = `toast show${error ? " error" : ""}`;
    clearTimeout(element.timer);
    element.timer = setTimeout(() => element.className = "toast", 2600);
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  }

  async function loadSummary() {
    const data = await api("/api/summary");
    $("#summary").innerHTML = [
      [data.total, "全部"], [data.unreviewed, "未校對"], [data.needs_review, "待複核"], [data.reviewed, "已通過"]
    ].map(([n, label]) => `<div><strong>${n}</strong><small>${label}</small></div>`).join("");
  }

  const PAGE_SIZE = 200;

  async function fetchPage(offset) {
    const params = new URLSearchParams({ q: $("#search").value, status: state.status, issue: state.issue, letter: state.letter, limit: String(PAGE_SIZE), offset: String(offset) });
    return (await api(`/api/spells?${params}`)).items;
  }

  function cardHtml(item) {
    return `
      <button type="button" class="spell-card ${item.id === state.current?.id ? "active" : ""}" data-id="${item.id}" role="option">
        <span class="letter">${escapeHtml(item.alphabet)}</span>
        <span><strong>${escapeHtml(item.name_zh)}</strong><small>${escapeHtml(item.name_en)}</small>${item.duplicate_name ? '<em class="dup">英文同名待判定</em>' : ""}</span>
        <i class="state ${item.review_status}" title="${item.review_status}"></i>
      </button>`;
  }

  function appendCards(items) {
    const list = $("#spell-list");
    list.insertAdjacentHTML("beforeend", items.map(cardHtml).join(""));
    $$(".spell-card:not([data-bound])", list).forEach(card => {
      card.dataset.bound = "";
      card.addEventListener("click", () => selectSpell(card.dataset.id));
    });
  }

  // keepLoaded reloads at least as many rows as are already shown, so refreshing
  // after an edit does not collapse the list back to the first page.
  async function loadList(selectId = null, { keepLoaded = false } = {}) {
    const token = ++state.listToken;
    const target = keepLoaded ? Math.max(state.items.length, PAGE_SIZE) : PAGE_SIZE;
    const items = [];
    let page;
    do {
      page = await fetchPage(items.length);
      if (token !== state.listToken) return;
      items.push(...page);
    } while (page.length === PAGE_SIZE && items.length < target);
    state.items = items;
    state.hasMore = page.length === PAGE_SIZE;
    const list = $("#spell-list");
    list.innerHTML = items.length ? "" : '<div class="empty-state"><p>目前篩選沒有條目。</p></div>';
    appendCards(items);
    if (selectId && state.items.some(item => item.id === selectId)) await selectSpell(selectId);
  }

  async function loadMore() {
    if (!state.hasMore || state.loadingMore) return;
    state.loadingMore = true;
    const token = state.listToken;
    try {
      const page = await fetchPage(state.items.length);
      if (token !== state.listToken) return;
      state.items.push(...page);
      state.hasMore = page.length === PAGE_SIZE;
      appendCards(page);
    } catch (error) { toast(error.message, true); }
    finally { state.loadingMore = false; }
  }

  async function itemAt(index) {
    while (index >= state.items.length && state.hasMore) {
      const before = state.items.length;
      await loadMore();
      if (state.items.length === before) break;
    }
    return state.items[index];
  }

  function formValues() {
    return Object.fromEntries(editable.map(name => [name, form.elements.namedItem(name).value]));
  }

  function setDirty(value) {
    state.dirty = value;
    $("#draft-state").textContent = value ? "未儲存 · 草稿保護中" : "已同步";
    $("#draft-state").classList.toggle("dirty", value);
  }

  async function saveDraft() {
    if (!state.current || !state.dirty) return;
    try {
      await api(`/api/spells/${state.current.id}/draft`, { method: "PUT", body: JSON.stringify({ revision_hash: state.current.revision_hash, fields: formValues() }) });
      $("#draft-state").textContent = "草稿已保存於本機";
    } catch (error) { toast(error.message, true); }
  }

  async function selectSpell(id) {
    if (state.dirty && !confirm("目前條目有尚未正式儲存的修改。草稿已保存在本機，仍要切換嗎？")) return;
    try {
      const spell = await api(`/api/spells/${id}`);
      state.current = spell;
      state.pdfPage = spell.pdf_page_start;
      renderSpell(spell);
      $$(".spell-card").forEach(card => card.classList.toggle("active", card.dataset.id === id));
    } catch (error) { toast(error.message, true); }
  }

  function renderSpell(spell) {
    $("#empty-state").hidden = true;
    form.hidden = false;
    $("#record-id").textContent = `${spell.id} · ${spell.entry_id}`;
    $("#record-title").textContent = spell.name_zh;
    $("#record-subtitle").textContent = spell.name_en;
    $("#review-badge").textContent = ({ reviewed: "已通過", needs_review: "需要校對", unreviewed: "尚未校對" })[spell.review_status];
    $("#review-badge").className = `badge ${spell.review_status}`;
    $("#revision-label").textContent = `rev ${spell.revision_hash.slice(0, 10)}`;
    $("#page-ribbon").textContent = `P. ${spell.pdf_page_start}${spell.pdf_page_end !== spell.pdf_page_start ? `–${spell.pdf_page_end}` : ""}`;
    editable.forEach(path => {
      const key = path.split(".")[1];
      form.elements.namedItem(path).value = spell[key] ?? "";
    });
    state.original = formValues();
    if (spell.draft?.payload && spell.draft.revision_hash === spell.revision_hash) {
      Object.entries(spell.draft.payload).forEach(([path, value]) => { if (form.elements.namedItem(path)) form.elements.namedItem(path).value = value ?? ""; });
      setDirty(JSON.stringify(formValues()) !== JSON.stringify(state.original));
      if (state.dirty) $("#draft-state").textContent = "已復原本機草稿";
    } else {
      setDirty(false);
      if (spell.draft) toast("偵測到舊版本草稿；為避免覆蓋新內容，未自動套用", true);
    }
    $("#raw-text").textContent = spell.raw_text || "";
    const tags = [...(spell.descriptors || []), ...(spell.levels || []).map(v => `${v.class_name} ${v.spell_level}`)];
    $("#taxonomy").innerHTML = tags.map(tag => `<span>${escapeHtml(tag)}</span>`).join("");
    const checked = new Set((spell.checks || []).filter(c => c.revision_hash === spell.revision_hash).map(c => c.check_type));
    $$("#checks input").forEach(input => input.checked = checked.has(input.value));
    updateApproval();
    renderEvidence(spell);
  }

  function renderEvidence(spell) {
    updatePdf();
    $("#sources").innerHTML = (spell.sources || []).map(source => `<span class="chip">${escapeHtml(source)}</span>`).join("") || '<span class="chip">未標示</span>';
    const confidence = `<span class="chip">解析信心 ${Math.round((spell.parse_confidence || 0) * 100)}%</span>`;
    const translation = spell.translation_status === "generated" ? '<div class="issue-card">此正文為生成翻譯，需人工確認。</div>' : "";
    $("#issues").innerHTML = confidence + translation + (spell.issues || []).map(issue => `<div class="issue-card"><strong>${escapeHtml(issue.issue_type)}</strong><br>${escapeHtml(issue.message)}</div>`).join("");
    $("#duplicate-block").hidden = !(spell.issues || []).some(issue => issue.issue_type === "duplicate_english_name");
    $("#conflict-block").hidden = !(spell.conflicts || []).length;
    $("#conflicts").innerHTML = (spell.conflicts || []).map(conflict => {
      const current = JSON.parse(conflict.value_a_json), incoming = JSON.parse(conflict.value_b_json);
      return `<article class="conflict-card" data-conflict="${conflict.id}"><code>${escapeHtml(conflict.field_path)}</code><small>目前值</small><p>${escapeHtml(current)}</p><small>合併進來的值</small><p>${escapeHtml(incoming)}</p><div class="conflict-actions"><button data-resolution="current">保留目前值</button><button data-resolution="incoming">採用合併值</button></div></article>`;
    }).join("");
    $$(".conflict-actions button").forEach(button => button.addEventListener("click", () => resolveConflict(button.closest(".conflict-card").dataset.conflict, button.dataset.resolution)));
    $("#history").innerHTML = (spell.history || []).map(item => `<li><strong>${actionLabel(item.action)} · ${escapeHtml(item.editor_name)}</strong><time>${new Date(item.occurred_at_utc).toLocaleString("zh-TW")}</time></li>`).join("") || "<li>尚無校對事件</li>";
  }

  function actionLabel(action) {
    return ({ update_fields: "儲存修改", set_check: "更新核對", approve_review: "通過校對", merge_spell: "合併條目", resolve_conflict: "解決衝突" })[action] || action;
  }

  function updatePdf() {
    if (!state.pdfPage) return;
    const url = `/source/pdf#page=${state.pdfPage}&view=FitH`;
    $("#pdf-frame").src = url;
    $("#pdf-open").href = url;
    $("#pdf-page").textContent = `PDF ${state.pdfPage}`;
  }

  function updateApproval() {
    $("#approve-button").disabled = state.dirty || $$("#checks input:checked").length !== 4;
  }

  async function saveChanges(event) {
    event.preventDefault();
    if (!state.current) return;
    const values = formValues();
    const changes = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== state.original[key]));
    if (!Object.keys(changes).length) return toast("沒有需要儲存的內容變更");
    try {
      const result = await api(`/api/spells/${state.current.id}/changes`, { method: "POST", body: JSON.stringify({ revision_hash: state.current.revision_hash, fields: changes, note: $("#review-note").value }) });
      state.current = result.spell;
      renderSpell(result.spell);
      await Promise.all([loadSummary(), loadList(null, { keepLoaded: true })]);
      toast(`修改已記錄：${result.event_id}`);
    } catch (error) { toast(error.message, true); }
  }

  async function toggleCheck(input) {
    if (!state.current) return;
    input.disabled = true;
    try {
      const result = await api(`/api/spells/${state.current.id}/checks`, { method: "POST", body: JSON.stringify({ revision_hash: state.current.revision_hash, check_type: input.value, checked: input.checked }) });
      state.current = result.spell;
      renderSpell(result.spell);
      toast(`${input.closest("label").querySelector("span").textContent}核對已${input.checked ? "完成" : "取消"}`);
    } catch (error) { input.checked = !input.checked; toast(error.message, true); }
    finally { input.disabled = false; }
  }

  async function approve() {
    try {
      const currentIndex = state.items.findIndex(item => item.id === state.current.id);
      const result = await api(`/api/spells/${state.current.id}/approve`, { method: "POST", body: JSON.stringify({ revision_hash: state.current.revision_hash, note: $("#review-note").value }) });
      toast(`已通過校對：${result.event_id}`);
      await Promise.all([loadSummary(), loadList(null, { keepLoaded: true })]);
      const next = (await itemAt(currentIndex + 1)) || state.items[state.items.length - 1];
      if (next && next.id !== state.current.id) await selectSpell(next.id); else renderSpell(result.spell);
    } catch (error) { toast(error.message, true); }
  }

  function openEditorDialog() { $("#editor-name").value = $("#editor-label").textContent === "尚未確認" ? "" : $("#editor-label").textContent; $("#editor-dialog").showModal(); }

  async function confirmEditor(event) {
    event.preventDefault();
    const name = $("#editor-name").value.trim();
    if (!name) return;
    try {
      await api("/api/settings/editor", { method: "POST", body: JSON.stringify({ editor_name: name }) });
      $("#editor-label").textContent = name;
      $("#editor-dialog").close();
      toast(`校對事件將署名為 ${name}`);
    } catch (error) { toast(error.message, true); }
  }

  async function openCommitDialog() {
    try {
      const preview = await api("/api/git/preview");
      state.commitPreview = preview;
      const warnings = [
        preview.operation_in_progress ? "Git 正在進行合併或 rebase。" : "",
        preview.staged.length ? `暫存區已有 ${preview.staged.length} 個檔案。` : "",
        !preview.identity_matches ? `校對者「${escapeHtml(preview.editor_name)}」與 Git 作者「${escapeHtml(preview.identity.name || "未設定")}」不同。` : "",
      ].filter(Boolean);
      $("#commit-preview").innerHTML = `
        <dl><dt>分支</dt><dd>${escapeHtml(preview.branch)}</dd><dt>Git 作者</dt><dd>${escapeHtml(preview.identity.name || "未設定")} &lt;${escapeHtml(preview.identity.email || "未設定")}&gt;</dd><dt>校對事件</dt><dd>${preview.event_count} 筆</dd><dt>檔案</dt><dd>${preview.files.length} 個</dd></dl>
        ${warnings.map(v => `<p class="commit-warning">${v}</p>`).join("")}
        <strong>將納入：</strong><ul>${preview.files.map(path => `<li>${escapeHtml(path)}</li>`).join("") || "<li>目前沒有校對變更</li>"}</ul>
        ${preview.other_changes.length ? `<strong>不會納入：</strong><ul>${preview.other_changes.map(path => `<li>${escapeHtml(path)}</li>`).join("")}</ul>` : ""}`;
      $("#confirm-commit").disabled = preview.operation_in_progress || preview.staged.length > 0 || preview.files.length === 0;
      $("#commit-dialog").showModal();
    } catch (error) { toast(error.message, true); }
  }

  async function createCommit(event) {
    event.preventDefault();
    const mismatch = !state.commitPreview.identity_matches;
    if (mismatch && !confirm("Git 作者與校對者名稱不同。確定以目前 Git 作者建立 commit，並保留事件中的校對者署名嗎？")) return;
    const button = $("#confirm-commit"); button.disabled = true; button.textContent = "驗證並建立中…";
    try {
      const result = await api("/api/git/commit", { method: "POST", body: JSON.stringify({ confirm_identity_mismatch: mismatch }) });
      $("#commit-dialog").close();
      toast(`Commit 已建立：${result.commit_hash.slice(0, 10)}；請自行檢查後 push`);
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; button.textContent = "建立 Commit"; }
  }

  async function openDuplicateDialog() {
    try {
      const result = await api(`/api/spells/${state.current.id}/duplicates`);
      state.duplicateGroup = result.items;
      $("#duplicate-comparison").innerHTML = result.items.map(item => `<article class="duplicate-card ${item.id === state.current.id ? "current" : ""}"><p class="mono">${escapeHtml(item.id)} · PDF ${item.pdf_page_start}</p><h3>${escapeHtml(item.name_zh)}</h3><p><strong>${escapeHtml(item.name_en)}</strong></p><p>${escapeHtml(item.school || "未標示學派")} · ${escapeHtml(item.components || "未標示成分")}</p><p class="excerpt">${escapeHtml(item.description_zh || item.description_en || "")}</p></article>`).join("");
      const others = result.items.filter(item => item.id !== state.current.id);
      $("#duplicate-related").innerHTML = others.map(item => `<option value="${item.id}">${escapeHtml(item.name_zh)} · PDF ${item.pdf_page_start} · ${item.id}</option>`).join("");
      $("#duplicate-note").value = "";
      $("#duplicate-dialog").showModal();
    } catch (error) { toast(error.message, true); }
  }

  async function saveDuplicateDecision(event) {
    event.preventDefault();
    const decision = $("#duplicate-decision").value;
    const related = ["merge", "variant"].includes(decision) ? $("#duplicate-related").value : null;
    if (decision === "merge" && !confirm("目前條目將標記為 merged 並指向保留條目；舊 ID 與歷程仍會保留。確定繼續？")) return;
    try {
      const result = await api(`/api/spells/${state.current.id}/duplicate-decision`, { method: "POST", body: JSON.stringify({ revision_hash: state.current.revision_hash, decision, related_spell_id: related, note: $("#duplicate-note").value }) });
      $("#duplicate-dialog").close();
      toast(`同名判定已記錄：${result.event_id}`);
      await Promise.all([loadSummary(), loadList(null, { keepLoaded: true })]);
      if (decision !== "merge") await selectSpell(state.current.id);
      else if (state.items.length) await selectSpell(state.items[0].id);
    } catch (error) { toast(error.message, true); }
  }

  async function resolveConflict(conflictId, resolution) {
    if (!confirm(`確定${resolution === "current" ? "保留目前值" : "採用合併值"}？此決定會建立新的解決事件。`)) return;
    try {
      const result = await api(`/api/conflicts/${conflictId}/resolve`, { method: "POST", body: JSON.stringify({ spell_id: state.current.id, revision_hash: state.current.revision_hash, resolution, note: "於校對介面解決欄位衝突" }) });
      state.current = result.spell; renderSpell(result.spell); await loadSummary(); toast(`衝突已解決：${result.event_id}`);
    } catch (error) { toast(error.message, true); }
  }

  function bind() {
    $("#alphabet").innerHTML = [''].concat("ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("")).map(letter => `<button data-letter="${letter}" class="${!letter ? "active" : ""}" title="${letter || "全部字母"}">${letter || "•"}</button>`).join("");
    $("#spell-list").addEventListener("scroll", event => { const list = event.currentTarget; if (list.scrollTop + list.clientHeight >= list.scrollHeight - 300) loadMore(); });
    $("#search").addEventListener("input", () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(loadList, 220); });
    $$(".filter").forEach(button => button.addEventListener("click", () => { state.status = button.dataset.status; $$(".filter").forEach(v => v.classList.toggle("active", v === button)); loadList(); }));
    $$(".issue-filter").forEach(button => button.addEventListener("click", () => { state.issue = state.issue === button.dataset.issue ? "" : button.dataset.issue; $$(".issue-filter").forEach(v => v.classList.toggle("active", v.dataset.issue === state.issue)); loadList(); }));
    $$("#alphabet button").forEach(button => button.addEventListener("click", () => { state.letter = button.dataset.letter; $$("#alphabet button").forEach(v => v.classList.toggle("active", v === button)); loadList(); }));
    form.addEventListener("input", event => {
      if (!event.target.matches("input,textarea") || event.target.closest("#checks")) return;
      setDirty(JSON.stringify(formValues()) !== JSON.stringify(state.original));
      updateApproval();
      clearTimeout(state.draftTimer); state.draftTimer = setTimeout(saveDraft, 700);
    });
    form.addEventListener("submit", saveChanges);
    $$("#checks input").forEach(input => input.addEventListener("change", () => toggleCheck(input)));
    $("#approve-button").addEventListener("click", approve);
    $("#editor-button").addEventListener("click", openEditorDialog);
    $("#commit-button").addEventListener("click", openCommitDialog);
    $("#confirm-commit").addEventListener("click", createCommit);
    $("#duplicate-button").addEventListener("click", openDuplicateDialog);
    $("#confirm-duplicate").addEventListener("click", saveDuplicateDecision);
    $("#confirm-editor").addEventListener("click", confirmEditor);
    $("#pdf-prev").addEventListener("click", () => { state.pdfPage = Math.max(1, state.pdfPage - 1); updatePdf(); });
    $("#pdf-next").addEventListener("click", () => { state.pdfPage += 1; updatePdf(); });
    window.addEventListener("beforeunload", event => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
    document.addEventListener("keydown", event => {
      if (event.key === "/" && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { event.preventDefault(); $("#search").focus(); }
      if (event.altKey && ["ArrowUp", "ArrowDown"].includes(event.key) && state.current) {
        event.preventDefault(); const i = state.items.findIndex(item => item.id === state.current.id); itemAt(i + (event.key === "ArrowDown" ? 1 : -1)).then(next => { if (next) selectSpell(next.id); });
      }
    });
  }

  async function start() {
    bind();
    try {
      await Promise.all([loadSummary(), loadList()]);
      if (state.items.length) await selectSpell(state.items[0].id);
      if (document.body.dataset.editorConfirmed !== "true") openEditorDialog();
    } catch (error) { toast(error.message, true); }
  }
  start();
})();
