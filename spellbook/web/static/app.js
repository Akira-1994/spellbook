(() => {
  const csrf = document.body.dataset.csrf;
  const PAGE_SIZE = 200;
  const FIELD_LABELS = {
    name_zh: "中文名稱", name_en: "英文名稱", school: "學派", subschool: "子學派", components: "成分",
    casting_time: "施法時間", range_text: "距離", target_text: "目標", area_text: "區域", effect_text: "效果",
    duration: "持續時間", saving_throw: "豁免", spell_resistance: "法術抗力", additional_costs: "額外代價",
    description_zh: "中文說明", description_en: "英文原文",
  };
  const FIELDS = Object.keys(FIELD_LABELS);
  const STAT_FIELDS = ["components", "casting_time", "range_text", "target_text", "area_text", "effect_text", "duration", "saving_throw", "spell_resistance", "additional_costs"];
  const REPLACED_BY = { edit: "編輯", rollback: "還原版本", restore_original: "還原原始內容" };

  const FILTER_STORAGE_KEY = "spellbook.filters";
  const state = {
    items: [], hasMore: false, loadingMore: false, listToken: 0, total: 0, letter: "", current: null, editing: false, formOriginal: null,
    summary: null, schools: [], classes: [], filters: { schools: new Set(), classId: "", levels: new Set() },
  };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const form = $("#spell-form");

  async function api(url, options = {}) {
    const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, ...(options.headers || {}) } });
    const payload = response.headers.get("content-type")?.includes("json") ? await response.json() : null;
    if (!response.ok) {
      const error = new Error(payload?.detail || `操作失敗（${response.status}）`);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function toast(message, error = false) {
    const element = $("#toast");
    element.textContent = message;
    element.className = `toast show${error ? " error" : ""}`;
    clearTimeout(element.timer);
    element.timer = setTimeout(() => element.className = "toast", 2800);
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  }

  function formatTime(value) {
    if (!value) return "";
    const d = new Date(value);
    const pad = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  // Text extracted from the PDF keeps its hard line wraps. Re-join wrapped lines
  // and only break paragraphs after sentence-ending punctuation or blank lines.
  function paragraphs(text) {
    const result = [];
    let current = "";
    for (const raw of String(text || "").split("\n")) {
      const line = raw.trim();
      if (!line) { if (current) result.push(current); current = ""; continue; }
      const needsSpace = /[A-Za-z0-9,;]$/.test(current) && /^[A-Za-z0-9(]/.test(line);
      current += (current && needsSpace ? " " : "") + line;
      if (/[。！？」』）)!?.:：]$/.test(line)) { result.push(current); current = ""; }
    }
    if (current) result.push(current);
    return result.map(p => `<p>${escapeHtml(p)}</p>`).join("");
  }

  function confirmDialog(title, body) {
    const dialog = $("#confirm-dialog");
    $("#confirm-title").textContent = title;
    $("#confirm-body").textContent = body;
    dialog.returnValue = "";
    dialog.showModal();
    return new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true }));
  }

  // ---- Index list -------------------------------------------------------

  async function loadSummary() {
    state.summary = await api("/api/summary");
    renderSummary();
  }

  function isFiltered() {
    const f = state.filters;
    return Boolean($("#search").value.trim() || state.letter || $("#edited-only").checked || f.schools.size || f.classId || f.levels.size);
  }

  function renderSummary() {
    if (!state.summary) return;
    const { total, edited } = state.summary;
    $("#summary").textContent = isFiltered()
      ? `符合 ${state.total} 個法術（共 ${total} 個）`
      : `共 ${total} 個法術${edited ? `，已修改 ${edited} 個` : ""}`;
  }

  async function fetchPage(offset) {
    const params = new URLSearchParams({ q: $("#search").value, letter: state.letter, edited: $("#edited-only").checked, limit: PAGE_SIZE, offset });
    state.filters.schools.forEach(school => params.append("school", school));
    if (state.filters.classId) params.set("class", state.filters.classId);
    state.filters.levels.forEach(level => params.append("level", level));
    const data = await api(`/api/spells?${params}`);
    state.total = data.total;
    return data.items;
  }

  function schoolName(key) {
    return state.schools.find(school => school.key === key)?.name ?? "未分類";
  }

  function schoolChips(keys, size = "") {
    return (keys?.length ? keys : ["unclassified"]).map(key =>
      `<span class="school-chip ${size}" data-school="${escapeHtml(key)}">${escapeHtml(schoolName(key))}</span>`).join("");
  }

  function selectedClassName() {
    return state.classes.find(c => String(c.id) === String(state.filters.classId))?.name ?? "";
  }

  function cardHtml(item) {
    const level = item.class_level ?? null;
    return `
      <button type="button" class="spell-card ${item.id === state.current?.id ? "active" : ""}" data-id="${item.id}" role="option">
        <span class="letter">${escapeHtml(item.alphabet)}</span>
        <span class="names"><strong>${escapeHtml(item.name_zh)}</strong><small>${escapeHtml(item.name_en)}</small></span>
        <span class="card-tags">
          ${level !== null ? `<i class="level-badge" title="${escapeHtml(selectedClassName())} ${level} 級">${escapeHtml(selectedClassName())} ${level}</i>` : ""}
          ${schoolChips(item.schools, "mini")}
          ${item.edited ? '<i class="edited-mark" title="已修改">已修改</i>' : ""}
        </span>
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
  async function loadList({ keepLoaded = false } = {}) {
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
    renderSummary();
    const list = $("#spell-list");
    const scroll = list.scrollTop;
    list.innerHTML = items.length ? "" : '<p class="list-empty">沒有符合的法術。</p>';
    appendCards(items);
    if (keepLoaded) list.scrollTop = scroll; else list.scrollTop = 0;
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

  function refreshCard(spell) {
    const index = state.items.findIndex(item => item.id === spell.id);
    if (index < 0) return;
    Object.assign(state.items[index], { name_zh: spell.name_zh, name_en: spell.name_en, alphabet: spell.alphabet, schools: spell.schools, edited: Boolean(spell.edited_at) });
    const card = $(`.spell-card[data-id="${spell.id}"]`);
    if (!card) return;
    card.outerHTML = cardHtml(state.items[index]);
    appendCards([]);
  }

  // ---- Spell page -------------------------------------------------------

  async function selectSpell(id) {
    if (state.editing && isDirty() && !(await confirmDialog("放棄未儲存的修改？", "目前的編輯內容尚未儲存，切換後會遺失。"))) return;
    try {
      const spell = await api(`/api/spells/${id}`);
      showSpell(spell);
      $$(".spell-card").forEach(card => card.classList.toggle("active", card.dataset.id === id));
      $(".spell-card.active")?.scrollIntoView({ block: "nearest" });
    } catch (error) { toast(error.message, true); }
  }

  function showSpell(spell) {
    state.current = spell;
    setEditing(false);
    renderView(spell);
    loadVersions();
  }

  function renderView(spell) {
    $("#empty-state").hidden = true;
    $("#view-schools").innerHTML = schoolChips(spell.schools, "large") +
      (spell.subschool ? `<span class="subschool">${escapeHtml(spell.subschool)}</span>` : "");
    $("#view-schools").title = spell.school ? `原文：${spell.school}` : "原文未標示學派";
    $("#view-name-zh").textContent = spell.name_zh;
    $("#view-name-en").textContent = spell.name_en;
    const badges = [];
    if (spell.edited_at) badges.push(`<span class="badge edited">已修改 · ${escapeHtml(formatTime(spell.edited_at))}</span>`);
    if (spell.translation_status === "generated") badges.push('<span class="badge generated" title="原書只有英文，此條中文為機器翻譯">機器翻譯</span>');
    $("#view-badges").innerHTML = badges.join("");
    $("#view-stats").innerHTML = STAT_FIELDS.filter(field => spell[field]).map(field =>
      `<div><dt>${FIELD_LABELS[field]}</dt><dd>${escapeHtml(spell[field])}</dd></div>`).join("");
    const levelChips = (spell.levels || []).map(v =>
      `<button type="button" class="chip level-chip ${v.kind === "domain" ? "domain" : ""}" data-class-id="${v.class_id}" data-level="${v.spell_level}" title="篩選所有${escapeHtml(v.class_name)} ${v.spell_level} 級法術">${escapeHtml(v.class_name)} ${v.spell_level}${v.note ? `（${escapeHtml(v.note)}）` : ""}</button>`);
    const plain = values => values.map(v => `<span class="chip">${escapeHtml(v)}</span>`);
    const groups = [
      ["等級", levelChips],
      ["描述詞", plain(spell.descriptors || [])],
      ["出處", plain([...(spell.sources || []), `原書 P.${spell.pdf_page_start}${spell.pdf_page_end !== spell.pdf_page_start ? `–${spell.pdf_page_end}` : ""}`])],
    ];
    $("#view-taxonomy").innerHTML = groups.filter(([, chips]) => chips.length).map(([label, chips]) =>
      `<div><span class="taxonomy-label">${label}</span>${chips.join("")}</div>`).join("");
    $$("#view-taxonomy .level-chip").forEach(chip => chip.addEventListener("click", () => filterByLevel(chip.dataset.classId, Number(chip.dataset.level))));
    $("#view-description").innerHTML = paragraphs(spell.description_zh) || '<p class="muted">（沒有說明）</p>';
    $("#view-english").hidden = !spell.description_en;
    $("#view-description-en").innerHTML = paragraphs(spell.description_en);
    $("#restore-original").disabled = !spell.edited_at;
  }

  // ---- Editing ----------------------------------------------------------

  function formValues() {
    return Object.fromEntries(FIELDS.map(name => [name, form.elements.namedItem(name).value]));
  }

  function isDirty() {
    return state.editing && JSON.stringify(formValues()) !== JSON.stringify(state.formOriginal);
  }

  function setEditing(editing) {
    state.editing = editing;
    $("#spell-view").hidden = editing || !state.current;
    form.hidden = !editing;
    if (editing) {
      FIELDS.forEach(name => { form.elements.namedItem(name).value = state.current[name] ?? ""; });
      state.formOriginal = formValues();
      updateFormState();
      form.elements.namedItem("name_zh").focus();
    }
    $(".page-pane").scrollTop = 0;
  }

  function updateFormState() {
    const changed = FIELDS.filter(name => formValues()[name] !== state.formOriginal[name]).length;
    $("#form-state").textContent = changed ? `已修改 ${changed} 個欄位` : "尚未修改";
    $("#save-button").disabled = !changed;
  }

  async function cancelEdit() {
    if (isDirty() && !(await confirmDialog("放棄修改？", "尚未儲存的內容會遺失。"))) return;
    setEditing(false);
  }

  async function saveEdit(event) {
    event.preventDefault();
    const values = formValues();
    const fields = Object.fromEntries(FIELDS.filter(name => values[name] !== state.formOriginal[name]).map(name => [name, values[name]]));
    if (!Object.keys(fields).length) return;
    $("#save-button").disabled = true;
    try {
      const spell = await api(`/api/spells/${state.current.id}`, { method: "PUT", body: JSON.stringify({ revision_hash: state.current.revision_hash, fields }) });
      showSpell(spell);
      await afterChange(spell);
      toast("已儲存，舊內容保留在版本紀錄中");
    } catch (error) {
      toast(error.message, true);
      updateFormState();
      if (error.status === 409) state.current = await api(`/api/spells/${state.current.id}`);
    }
  }

  async function afterChange(spell) {
    await Promise.all([loadSummary(), loadTaxonomy()]);
    // Edits can move a spell in or out of the "edited" or school filters.
    if ($("#edited-only").checked || state.filters.schools.size) await loadList({ keepLoaded: true }); else refreshCard(spell);
  }

  // ---- Filters ----------------------------------------------------------

  async function loadTaxonomy() {
    const data = await api("/api/taxonomy");
    state.schools = data.schools;
    state.classes = data.classes;
    renderFilterOptions();
  }

  function renderFilterOptions() {
    const f = state.filters;
    $("#school-filters").innerHTML = state.schools.map(school =>
      `<button type="button" class="school-chip filter ${f.schools.has(school.key) ? "selected" : ""}" data-school="${school.key}" aria-pressed="${f.schools.has(school.key)}">${escapeHtml(school.name)}<small>${school.count}</small></button>`).join("");
    const groups = [["class", "職業"], ["domain", "領域"], ["other", "其他"]];
    $("#class-filter").innerHTML = '<option value="">全部職業與領域</option>' + groups.map(([kind, label]) => {
      const options = state.classes.filter(c => c.kind === kind).map(c =>
        `<option value="${c.id}">${escapeHtml(c.name)}（${c.count}）</option>`).join("");
      return options ? `<optgroup label="${label}">${options}</optgroup>` : "";
    }).join("");
    $("#class-filter").value = f.classId;
    $("#level-filters").innerHTML = Array.from({ length: 10 }, (_, level) =>
      `<button type="button" class="level-button ${f.levels.has(level) ? "selected" : ""}" data-level="${level}" aria-pressed="${f.levels.has(level)}">${level}</button>`).join("");
    const active = f.schools.size + (f.classId ? 1 : 0) + f.levels.size;
    $("#filter-count").hidden = !active;
    $("#filter-count").textContent = active ? `${active} 項` : "";
    $("#clear-filters").hidden = !active;
  }

  function saveFilters() {
    const f = state.filters;
    try { localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify({ schools: [...f.schools], classId: f.classId, levels: [...f.levels] })); } catch { /* storage unavailable */ }
  }

  function restoreFilters() {
    try {
      const saved = JSON.parse(localStorage.getItem(FILTER_STORAGE_KEY) || "null");
      if (!saved) return;
      const schools = new Set(state.schools.map(s => s.key));
      state.filters.schools = new Set((saved.schools || []).filter(key => schools.has(key)));
      state.filters.classId = state.classes.some(c => String(c.id) === String(saved.classId)) ? String(saved.classId) : "";
      state.filters.levels = new Set((saved.levels || []).filter(level => Number.isInteger(level) && level >= 0 && level <= 9));
    } catch { /* ignore corrupt or unavailable storage */ }
  }

  function filtersChanged() {
    saveFilters();
    renderFilterOptions();
    loadList();
  }

  function filterByLevel(classId, level) {
    $("#search").value = "";
    $("#edited-only").checked = false;
    state.letter = "";
    $$("#alphabet button").forEach(button => button.classList.toggle("active", button.dataset.letter === ""));
    state.filters = { schools: new Set(), classId: String(classId), levels: new Set([level]) };
    $("#filters").open = true;
    filtersChanged();
    toast(`已篩選：${selectedClassName()} ${level} 級`);
  }

  // ---- Version history --------------------------------------------------

  async function loadVersions() {
    const spell = state.current;
    const data = await api(`/api/spells/${spell.id}/versions`);
    if (state.current !== spell) return;
    const now = Object.fromEntries(FIELDS.map(name => [name, spell[name] ?? ""]));
    const currentItem = `<li class="version current"><div class="version-head"><strong>目前內容</strong><span>${spell.edited_at ? escapeHtml(formatTime(spell.edited_at)) + " 儲存" : "原始內容"}</span></div></li>`;
    const items = data.items.map(version => {
      const changed = FIELDS.filter(name => (version.content[name] ?? "") !== now[name]);
      const diff = changed.map(name => `
        <div class="diff-row"><p class="diff-label">${FIELD_LABELS[name]}</p>
          <p class="diff-old"><span>此版本</span>${escapeHtml(truncate(version.content[name]))}</p>
          <p class="diff-new"><span>目前</span>${escapeHtml(truncate(now[name]))}</p></div>`).join("");
      return `<li class="version">
        <div class="version-head"><strong>${version.content_saved_at ? escapeHtml(formatTime(version.content_saved_at)) + " 的版本" : "原始內容"}</strong>
          <span>${escapeHtml(formatTime(version.replaced_at))} 因${REPLACED_BY[version.replaced_by]}被取代</span></div>
        <details><summary>${changed.length ? `與目前相差 ${changed.length} 個欄位` : "與目前內容相同"}</summary>${diff}</details>
        <button type="button" class="secondary small" data-version="${version.id}" ${changed.length ? "" : "disabled"}>還原到此版本</button>
      </li>`;
    }).join("");
    $("#history").innerHTML = `<ol class="version-list">${currentItem}${items}</ol>${data.items.length ? "" : '<p class="muted">這個法術還沒有修改過。</p>'}`;
    $$("#history [data-version]").forEach(button => button.addEventListener("click", () => rollback(Number(button.dataset.version))));
  }

  function truncate(value, length = 160) {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    if (!text) return "（空白）";
    return text.length > length ? `${text.slice(0, length)}…` : text;
  }

  async function rollback(versionId) {
    if (!(await confirmDialog("還原到這個版本？", "目前的內容會先存進版本紀錄，之後仍可以再還原回來。"))) return;
    await replaceWith(`/api/spells/${state.current.id}/rollback`, { version_id: versionId }, "已還原到選取的版本");
  }

  async function restoreOriginal() {
    if (!(await confirmDialog("還原成原始內容？", "法術會回到原書擷取的內容。目前的內容會先存進版本紀錄。"))) return;
    await replaceWith(`/api/spells/${state.current.id}/restore-original`, {}, "已還原成原始內容");
  }

  async function replaceWith(url, body, message) {
    if (state.editing && isDirty() && !(await confirmDialog("放棄未儲存的修改？", "還原會取代目前編輯中的內容。"))) return;
    try {
      const spell = await api(url, { method: "POST", body: JSON.stringify({ revision_hash: state.current.revision_hash, ...body }) });
      showSpell(spell);
      await afterChange(spell);
      toast(message);
    } catch (error) { toast(error.message, true); }
  }

  // ---- Wiring -----------------------------------------------------------

  async function step(offset) {
    if (!state.current) return;
    const index = state.items.findIndex(item => item.id === state.current.id);
    const next = await itemAt(index + offset);
    if (next && index + offset >= 0) selectSpell(next.id);
  }

  function placeHistory(narrow) {
    const pane = $(".history-pane");
    if (narrow) $(".page-pane").append(pane); else $(".workspace").append(pane);
  }

  function bind() {
    const narrow = matchMedia("(max-width: 1180px)");
    narrow.addEventListener("change", event => placeHistory(event.matches));
    placeHistory(narrow.matches);
    $("#alphabet").innerHTML = [""].concat("ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("")).map(letter =>
      `<button type="button" data-letter="${letter}" class="${letter ? "" : "active"}" title="${letter || "全部字母"}">${letter || "全"}</button>`).join("");
    $$("#alphabet button").forEach(button => button.addEventListener("click", () => {
      state.letter = button.dataset.letter;
      $$("#alphabet button").forEach(v => v.classList.toggle("active", v === button));
      loadList();
    }));
    $("#search").addEventListener("input", () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => loadList(), 220); });
    $("#school-filters").addEventListener("click", event => {
      const chip = event.target.closest("[data-school]");
      if (!chip) return;
      const schools = state.filters.schools;
      if (schools.has(chip.dataset.school)) schools.delete(chip.dataset.school); else schools.add(chip.dataset.school);
      filtersChanged();
    });
    $("#class-filter").addEventListener("change", event => { state.filters.classId = event.target.value; filtersChanged(); });
    $("#level-filters").addEventListener("click", event => {
      const button = event.target.closest("[data-level]");
      if (!button) return;
      const level = Number(button.dataset.level), levels = state.filters.levels;
      if (levels.has(level)) levels.delete(level); else levels.add(level);
      filtersChanged();
    });
    $("#clear-filters").addEventListener("click", () => {
      state.filters = { schools: new Set(), classId: "", levels: new Set() };
      filtersChanged();
    });
    if (matchMedia("(max-width: 760px)").matches) $("#filters").open = false;
    $("#edited-only").addEventListener("change", () => loadList());
    $("#spell-list").addEventListener("scroll", event => {
      const list = event.currentTarget;
      if (list.scrollTop + list.clientHeight >= list.scrollHeight - 300) loadMore();
    });
    $("#edit-button").addEventListener("click", () => setEditing(true));
    $("#cancel-button").addEventListener("click", cancelEdit);
    form.addEventListener("submit", saveEdit);
    form.addEventListener("input", updateFormState);
    $("#restore-original").addEventListener("click", restoreOriginal);
    window.addEventListener("beforeunload", event => { if (isDirty()) { event.preventDefault(); event.returnValue = ""; } });
    document.addEventListener("keydown", event => {
      const typing = /INPUT|TEXTAREA/.test(document.activeElement.tagName);
      if (event.key === "/" && !typing) { event.preventDefault(); $("#search").focus(); }
      if (state.editing && event.key === "Escape" && !$("#confirm-dialog").open) { event.preventDefault(); cancelEdit(); }
      if (state.editing && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); form.requestSubmit(); }
      if (event.altKey && ["ArrowUp", "ArrowDown"].includes(event.key)) { event.preventDefault(); step(event.key === "ArrowDown" ? 1 : -1); }
    });
  }

  // Keeps the desktop launcher alive while a page is open, and tells the user
  // when the app has already exited.
  async function ping() {
    try {
      const response = await fetch("/api/ping", { cache: "no-store" });
      $("#offline-banner").hidden = response.ok;
    } catch { $("#offline-banner").hidden = false; }
  }

  async function start() {
    bind();
    setInterval(ping, 30000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) ping(); });
    try {
      await Promise.all([loadSummary(), loadTaxonomy()]);
      restoreFilters();
      renderFilterOptions();
      await loadList();
      if (state.items.length) await selectSpell(state.items[0].id);
    } catch (error) { toast(error.message, true); }
  }
  start();
})();
