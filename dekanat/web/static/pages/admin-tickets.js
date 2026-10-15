/* Кабинет сотрудника: вкладка «Обращения»: список, фильтры, карточка, статусы, массовые действия. Подключается после admin.js (общие помощники и каркас) и common.js. */
"use strict";

// ---------------------------------------------------------------- обращения
async function viewTickets(panel) {
  // одна «бумага»: слева спокойный список, справа карточка обращения
  const app = el("div", "t-app");
  const inbox = el("section", "t-inbox");
  const rightBox = el("section", "t-case");
  app.append(inbox, rightBox);
  panel.appendChild(app);

  // ---- шапка списка: заголовок, сводка и режим выбора
  const headSub = el("p", "t-sub", "");
  const headTitle = el("div");
  headTitle.append(el("h2", null, "Обращения"), headSub);
  const selectBtn = el("button", "t-link", "Выбрать", { type: "button", "aria-pressed": "false" });
  const inboxHead = el("div", "t-head");
  inboxHead.append(headTitle, selectBtn);

  // ---- фильтры живут в окошке под кнопкой: список начинается сразу
  const filters = el("div", "filters");
  const mk = (label, node) => { filters.appendChild(field(label, node)); return node; };
  const from = mk("С даты", el("input", null, null, { type: "date" }));
  const to = mk("По дату", el("input", null, null, { type: "date" }));
  const type = mk("Тип", el("select"));
  const topic = mk("Тема", el("select"));
  from.value = ticketFilter.from;
  to.value = ticketFilter.to;
  const csvLink = el("a", "t-link", "Выгрузить CSV", { href: "#", download: "" });
  const resetBtn = button("Сбросить", () => {
    Object.assign(ticketFilter, { from: "", to: "", type: "", status: "", topic: "" });
    from.value = to.value = type.value = topic.value = "";
    load();
  }, "t-link");
  const popActions = el("div", "filter-pop-actions");
  popActions.append(resetBtn, csvLink);
  const filterPop = el("div", "filter-pop hidden", null, { role: "dialog", "aria-label": "Фильтры обращений" });
  filterPop.append(filters, popActions);
  const filterDot = el("i", "badge-dot hidden", null, { "aria-hidden": "true" });
  const filterBtn = el("button", "t-iconbtn", null, { type: "button", title: "Фильтры", "aria-label": "Фильтры", "aria-haspopup": "dialog", "aria-expanded": "false" });
  filterBtn.append(icon("filter"), filterDot);
  const closePop = () => {
    filterPop.classList.add("hidden");
    filterBtn.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", onPopDown, true);
    document.removeEventListener("keydown", onPopKey);
  };
  const onPopDown = (e) => { if (!filterPop.contains(e.target) && !filterBtn.contains(e.target)) closePop(); };
  const onPopKey = (e) => { if (e.key === "Escape") { closePop(); filterBtn.focus(); } };
  filterBtn.addEventListener("click", () => {
    if (!filterPop.classList.contains("hidden")) return closePop();
    filterPop.classList.remove("hidden");
    filterBtn.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", onPopDown, true);
    document.addEventListener("keydown", onPopKey);
    from.focus();
  });
  const filterCount = () => [ticketFilter.from, ticketFilter.to, ticketFilter.type, ticketFilter.topic].filter(Boolean).length;
  const updateFilterBadge = () => {
    const n = filterCount();
    filterDot.classList.toggle("hidden", !n);
    filterBtn.setAttribute("aria-label", n ? `Фильтры (включено: ${n})` : "Фильтры");
  };

  const searchInput = el("input", null, null, { type: "search", placeholder: "Имя, номер или тема", "aria-label": "Найти обращение по имени, номеру или теме", autocomplete: "off" });
  const searchBox = el("label", "t-search");
  searchBox.append(icon("search"), searchInput);
  const toolbar = el("div", "t-toolbar");
  toolbar.append(searchBox, filterBtn, filterPop);

  const tally = el("div", "t-tabs", null, { role: "group", "aria-label": "Статус" });
  const list = el("div", "items", null, { role: "listbox", "aria-label": "Обращения" });

  // ---- выбор нескольких обращений: один статус (и, если нужно, один ответ) сразу для всех выбранных
  const picked = new Set();
  const byId = new Map(); // последние загруженные обращения: поиск и отмена берут данные отсюда
  let needle = "";
  let selecting = false;
  const bulkBar = el("div", "bulk-bar hidden");
  const bulkReply = el("textarea", "bulk-reply", null, { rows: "2", maxlength: "2000", placeholder: "Один ответ всем выбранным студентам (необязательно)", "aria-label": "Ответ студентам выбранных обращений" });
  const bulkReplyRow = el("div", "bulk-reply-row hidden");
  bulkReplyRow.appendChild(bulkReply);
  inbox.append(inboxHead, toolbar, tally, bulkBar, list);

  let meta = null;
  let firstLoad = true;
  const replyPresets = await api("GET", "/api/admin/reply-templates").then((r) => r.templates || []).catch(() => []); // заготовки необязательны: без них страница работает
  const fill = (sel, values, titles, current_) => {
    clear(sel);
    sel.appendChild(el("option", null, "все", { value: "" }));
    for (const v of values) sel.appendChild(el("option", null, titles ? titles[v] || v : v, { value: v }));
    sel.value = current_;
  };
  const query = (extra = {}) => new URLSearchParams({ date_from: ticketFilter.from, date_to: ticketFilter.to, type: ticketFilter.type, status: ticketFilter.status, topic: ticketFilter.topic, limit: "50", ...extra });
  const rows = () => [...list.querySelectorAll(".ticket")];
  const visibleRows = () => rows().filter((x) => !x.classList.contains("hidden"));
  const visibleIds = () => visibleRows().map((x) => Number(x.dataset.id));
  const filtersOn = () => !!(ticketFilter.from || ticketFilter.to || ticketFilter.type || ticketFilter.topic || ticketFilter.status);
  const searchText = (t) => foldText([t.id, `№ ${t.id}`, `№${t.id}`, t.student, t.topic, TYPE_TITLES[t.type], t.request].filter(Boolean).join(" "));
  const syncPicks = () => {
    for (const x of rows()) {
      const on = picked.has(Number(x.dataset.id));
      x.classList.toggle("picked", on);
      x.querySelector(".t-pick").checked = on;
    }
    drawBulk();
  };
  /** Режим выбора: флажки у строк и полоса действий появляются только в нём (или когда отметили первую строку). */
  const setSelecting = (on) => {
    selecting = on;
    list.classList.toggle("selecting", on);
    selectBtn.setAttribute("aria-pressed", String(on));
    selectBtn.textContent = on ? "Готово" : "Выбрать";
    if (!on) {
      picked.clear();
      bulkReply.value = "";
      bulkReplyRow.classList.add("hidden");
    }
    syncPicks();
  };
  selectBtn.addEventListener("click", () => setSelecting(!selecting));
  /** Поиск работает по уже загруженным обращениям; скрытые поиском из выбора выпадают, чтобы массовое действие не задело невидимое. */
  function applySearch() {
    let shown = 0;
    for (const x of rows()) {
      const id = Number(x.dataset.id);
      const hit = !needle || searchText(byId.get(id)).includes(needle);
      x.classList.toggle("hidden", !hit);
      if (hit) shown += 1;
      else picked.delete(id);
    }
    const none = list.querySelector(".no-match");
    if (none) none.classList.toggle("hidden", !(needle && !shown && rows().length));
    syncPicks();
  }
  searchInput.addEventListener("input", () => { needle = foldText(searchInput.value.trim()); applySearch(); });
  searchInput.addEventListener("keydown", (e) => { if (e.key === "Escape" && searchInput.value) { searchInput.value = ""; needle = ""; applySearch(); } });

  /** Статусы — тихие вкладки с подчёркиванием: слово и число, без цветных плашек. */
  function drawTally(per, all) {
    clear(tally);
    const add = (value, label, n) => {
      const on = value === ticketFilter.status;
      const b = el("button", n || on ? null : "zero", null, { type: "button", "aria-pressed": String(on), title: `${label}: ${n}` });
      if (value) b.dataset.st = value;
      b.appendChild(document.createTextNode(label));
      // число — только у того, что требует действий; итог по закрытым — в подсказке, общий — в шапке списка
      if ((value === "new" || value === "in_progress") && n) b.appendChild(el("span", "n", String(n)));
      b.addEventListener("click", () => { ticketFilter.status = value; load(); });
      tally.appendChild(b);
    };
    add("", "Все", all);
    for (const [x, c] of per) add(x, STATUS_PLURAL[x], c);
    // если вкладки не помещаются, правый край мягко затухает: видно, что их можно прокрутить
    requestAnimationFrame(() => tally.classList.toggle("scrolls", tally.scrollWidth > tally.clientWidth + 1));
  }

  /** Полоса выбора: пока ничего не отмечено — тонкая строка «Выбрать все», с выбором — тёмная панель с действиями. */
  function drawBulk() {
    const ids = visibleIds();
    clear(bulkBar);
    bulkBar.classList.toggle("hidden", !selecting || !ids.length);
    bulkBar.classList.toggle("active", picked.size > 0);
    if (!selecting || !ids.length) return;
    const all = el("input", null, null, { type: "checkbox", "aria-label": "Выбрать все обращения в списке" });
    all.checked = ids.every((i) => picked.has(i));
    all.indeterminate = !all.checked && ids.some((i) => picked.has(i));
    all.addEventListener("change", () => { for (const i of ids) (all.checked ? picked.add(i) : picked.delete(i)); syncPicks(); });
    const head = el("div", "bulk-head");
    head.append(tick(all), el("span", "bulk-count", picked.size ? `Выбрано: ${picked.size}` : "Выбрать все"));
    if (!picked.size) {
      head.appendChild(el("span", "bulk-total", `${ids.length} ${plural(ids.length, ["обращение", "обращения", "обращений"])}`));
      bulkBar.appendChild(head);
      return;
    }
    // два спокойных ряда: «Выбрано: N» и крестик, ниже четыре равные кнопки
    const drop = el("button", "bulk-aux icon-only", null, { type: "button", "aria-label": "Снять выбор", title: "Снять выбор" });
    drop.appendChild(icon("x"));
    drop.addEventListener("click", () => { picked.clear(); syncPicks(); });
    head.appendChild(drop);
    const actions = el("div", "bulk-actions", null, { role: "group", "aria-label": "Статус для выбранных" });
    for (const st of ["in_progress", "done", "rejected"]) {
      const b = el("button", "bulk-st", STATUS_LABEL[st], { type: "button" });
      b.dataset.st = st;
      b.addEventListener("click", () => applyBulk(st));
      actions.appendChild(b);
    }
    const replyOpen = !bulkReplyRow.classList.contains("hidden");
    const replyBtn = el("button", "bulk-st", "Ответ", { type: "button", "aria-expanded": String(replyOpen), title: "Один ответ всем выбранным студентам" });
    replyBtn.addEventListener("click", () => { bulkReplyRow.classList.toggle("hidden"); drawBulk(); if (!bulkReplyRow.classList.contains("hidden")) bulkReply.focus(); });
    actions.appendChild(replyBtn);
    bulkBar.append(head, actions, bulkReplyRow);
  }

  const afterChange = async (id) => {
    await load();
    refreshCounts();
    if (openTicketId && (id === undefined || id === openTicketId)) showTicket(openTicketId);
  };

  async function applyBulk(status) {
    const ids = [...picked].filter((i) => visibleIds().includes(i)); // скрытое фильтром или поиском не трогаем
    if (!ids.length) return;
    const reply = bulkReply.value.trim();
    if (status === "rejected" || ids.length > 5 || reply) {
      const ok = await askConfirm({
        title: `Поставить статус «${STATUS_TITLES[status]}»?`,
        text: `Обращений: ${ids.length}.` + (reply ? " Все эти студенты получат один и тот же ответ." : ""),
        ok: "Применить",
        danger: status === "rejected",
      });
      if (!ok) return;
    }
    const before = ids.map((id) => [id, (byId.get(id) || {}).status]);
    const res = await guarded(() => api("POST", "/api/admin/tickets/bulk", { ids, status, ...(reply ? { reply } : {}) }));
    if (!res) return;
    setSelecting(false);
    const n = res.updated.length;
    const text = res.missing.length ? `Обновлено: ${n}, не найдено: ${res.missing.length}` : `«${STATUS_LABEL[status]}»: ${n} ${plural(n, ["обращение", "обращения", "обращений"])}`;
    // отмена возвращает прежние статусы; общий ответ студентам уже ушёл, поэтому после него отмены нет
    const undo = reply ? undefined : async () => {
      const groups = new Map();
      for (const [id, st] of before) if (st && res.updated.includes(id)) groups.set(st, [...(groups.get(st) || []), id]);
      for (const [st, group] of groups) await guarded(() => api("POST", "/api/admin/tickets/bulk", { ids: group, status: st }));
      toast("Статусы возвращены");
      await afterChange();
    };
    toast(text, undo);
    await afterChange(undefined);
  }

  /** Один щелчок «Обработано» в строке; «Отменить» в сообщении возвращает прежний статус. */
  async function quickDone(t) {
    const ok = await guarded(async () => { await api("PATCH", `/api/admin/tickets/${t.id}`, { status: "done" }); return true; });
    if (!ok) return;
    toast(`№ ${t.id}: обработано`, async () => {
      await guarded(() => api("PATCH", `/api/admin/tickets/${t.id}`, { status: t.status }));
      toast("Статус возвращён");
      await afterChange(t.id);
    });
    await afterChange(t.id);
  }

  const narrow = () => window.matchMedia("(max-width: 980px)").matches;
  const select = (it, id, byUser = false) => {
    openTicketId = id;
    for (const x of rows()) { x.classList.toggle("on", x === it); x.setAttribute("aria-selected", String(x === it)); }
    if (it && it.scrollIntoView) it.scrollIntoView({ block: "nearest" });
    showTicket(id).then(() => { if (byUser && narrow()) rightBox.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" }); });
  };

  async function load() {
    const res = await guarded(() => api("GET", "/api/admin/tickets?" + query()));
    if (!res) return;
    if (!meta) {
      meta = res;
      fill(type, res.types, TYPE_TITLES, ticketFilter.type);
      fill(topic, res.topics, null, ticketFilter.topic);
    }
    csvLink.href = "/api/admin/tickets/export.csv?" + query(); // выгрузка с теми же фильтрами, что в списке
    const per = meta.statuses.map((x) => [x, (res.counts || {})[x] || 0]); // счётчики приходят вместе со списком: один запрос вместо пяти
    const all = per.reduce((n, [, c]) => n + c, 0);
    drawTally(per, all);
    const fresh = (res.counts || {}).new || 0;
    headSub.textContent = `${all} ${plural(all, ["обращение", "обращения", "обращений"])}` + (fresh ? ` · ${fresh} ${plural(fresh, ["новое", "новых", "новых"])}` : "");
    updateFilterBadge();
    for (const id of [...picked]) if (!res.items.some((t) => t.id === id)) picked.delete(id); // выбор живёт только среди показанного
    clear(list);
    byId.clear();
    for (const t of res.items) byId.set(t.id, t);
    if (!res.items.length) list.appendChild(empty("Обращений нет", filtersOn() ? "По этим условиям ничего не нашлось — попробуйте убрать часть фильтров." : "Когда студент передаст обращение из анкеты, оно появится здесь."));
    res.items.forEach((t, i) => {
      const closed = t.status === "done" || t.status === "rejected";
      const name = t.student || "";
      const topicText = t.topic || TYPE_TITLES[t.type] || t.type;
      const it = el("div", `item ticket st-${t.status}${t.status === "new" ? " is-new" : ""}${closed ? " closed" : ""}${t.id === openTicketId ? " on" : ""}${picked.has(t.id) ? " picked" : ""}`, null, { role: "option", tabindex: "0", "aria-selected": String(t.id === openTicketId), "data-id": String(t.id) });
      it.style.animationDelay = `${Math.min(i, 8) * 30}ms`;
      const pick = el("input", "t-pick", null, { type: "checkbox", "aria-label": `Выбрать обращение № ${t.id}${name ? `, ${name}` : ""}` });
      pick.checked = picked.has(t.id);
      pick.addEventListener("keydown", (e) => e.stopPropagation()); // пробел в флажке не открывает обращение
      pick.addEventListener("change", () => {
        (pick.checked ? picked.add(t.id) : picked.delete(t.id));
        if (!selecting) setSelecting(true); // отметили первую строку — включается режим выбора
        else { it.classList.toggle("picked", pick.checked); drawBulk(); }
      });
      // слева точка статуса (при наведении и в режиме выбора — флажок), в центре имя и суть, справа время.
      // Без имени (анкета без ФИО) заголовком идёт тема.
      const lead = el("span", "t-lead");
      lead.append(el("i", `dot ${t.status}`, null, { "aria-hidden": "true" }), tick(pick));
      const h = el("h3", null, name || topicText);
      h.appendChild(el("span", "sr", ` — ${STATUS_TITLES[t.status] || t.status}`));
      const main = el("div", "t-main");
      main.append(h, el("p", "item-meta", [name ? topicText : "", `№ ${t.id}`].filter(Boolean).join(" · ")));
      // при наведении на место времени встаёт «Обработано»: строка не меняет высоту
      const side = el("div", `t-side${closed ? "" : " has-quick"}`);
      side.appendChild(el("time", "t-time", relTime(t.created_at), { title: fmtWhen(t.created_at) }));
      if (!closed) {
        const quick = el("button", "t-quick", null, { type: "button", title: "Отметить обработанным", "aria-label": `Отметить обработанным: обращение № ${t.id}` });
        quick.appendChild(icon("check"));
        quick.addEventListener("click", (e) => { e.stopPropagation(); quickDone(t); });
        quick.addEventListener("keydown", (e) => e.stopPropagation());
        side.appendChild(quick);
      }
      it.append(lead, main, side);
      it.addEventListener("click", () => select(it, t.id, true));
      it.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(it, t.id, true); return; }
        const step = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
        const all2 = visibleRows();
        const next = step && all2[all2.indexOf(it) + step];
        if (next) { e.preventDefault(); next.focus(); next.click(); }
      });
      list.appendChild(it);
    });
    list.appendChild(el("div", "empty no-match hidden", "Ничего не нашлось — попробуйте другое имя или номер."));
    // зашли на вкладку — сразу показываем первое обращение (новое в приоритете): лишний клик не нужен
    if (firstLoad && !openTicketId && res.items.length) {
      firstLoad = false;
      const target = res.items.find((t) => t.status === "new") || res.items[0];
      select(rows()[res.items.indexOf(target)], target.id);
    }
    firstLoad = false;
    applySearch();
  }

  async function showTicket(id) {
    const t = await guarded(() => api("GET", `/api/admin/tickets/${id}`));
    if (!t) return;
    clear(rightBox);
    const fieldsMap = (t.payload || {}).fields || {};
    const fieldsEntries = Object.entries(fieldsMap);
    const name = String(fieldsMap["ФИО"] || "").trim();
    const topicText = t.topic || TYPE_TITLES[t.type] || t.type;

    // шапка: кто и с чем; справа — переход к соседним обращениям
    const head = el("header", "case-head");
    const titles = el("div", "case-titles");
    titles.append(el("h2", null, name || `Обращение № ${t.id}`), el("p", "case-sub", [`№ ${t.id}`, topicText, fmtWhen(t.created_at)].join(" · ")));
    const vis = visibleRows();
    const at = vis.findIndex((x) => Number(x.dataset.id) === t.id);
    const nav = el("div", "case-nav", null, { role: "group", "aria-label": "Соседние обращения" });
    const step = (d, label, ic) => {
      const b = el("button", "icon-btn", null, { type: "button", title: label, "aria-label": label });
      b.appendChild(icon(ic));
      b.disabled = at < 0 || !vis[at + d];
      b.addEventListener("click", () => vis[at + d] && vis[at + d].click());
      return b;
    };
    nav.append(step(-1, "Предыдущее (↑)", "up"), el("span", "pos tabnum", at >= 0 ? `${at + 1} / ${vis.length}` : ""), step(1, "Следующее (↓)", "down"));
    head.append(titles, nav);

    const body = el("div", "case-body");
    /** Раздел карточки: тихий заголовок, справа необязательная пометка. */
    const section = (title, aside, ...nodes) => {
      const sec = el("section", "case-sec");
      const line = el("div", "sec-line");
      line.appendChild(el("h3", "sec-title", title));
      if (aside) line.appendChild(el("span", "sec-aside", aside));
      sec.append(line, ...nodes);
      return sec;
    };

    // статус: один щелчок, «Отменить» в сообщении возвращает прежний
    let status = t.status;
    const segBox = el("div", "case-status");
    const drawSeg = () => {
      const group = seg(meta.statuses.map((st) => [st, STATUS_LABEL[st]]), status, (st) => setStatus(st), "Статус обращения", (st) => st);
      group.classList.add("calm");
      clear(segBox).appendChild(group);
    };
    async function setStatus(v, quiet = false) {
      if (v === status) return;
      const before = status;
      status = v;
      drawSeg();
      const ok = await guarded(async () => { await api("PATCH", `/api/admin/tickets/${t.id}`, { status: v }); return true; });
      if (!ok) { status = before; drawSeg(); return; }
      if (!quiet) toast(`Статус: ${STATUS_TITLES[v]}`, () => { setStatus(before, true); toast("Статус возвращён"); });
      load();
      refreshHistory();
      refreshCounts();
    }
    drawSeg();
    body.appendChild(segBox);
    if (t.similar && t.similar.length) {
      body.appendChild(el("p", "dup-note", `Этот студент уже обращался: ${t.similar.map((s) => `№ ${s.id} (${STATUS_TITLES[s.status] || s.status})`).join(", ")}.`));
    }

    // заявка: что нужно и данные из анкеты
    const requestNodes = [];
    if ((t.payload || {}).request || fieldsEntries.length) {
      const kv = el("dl", "kv");
      if ((t.payload || {}).request) kv.append(el("dt", null, "Что нужно"), el("dd", null, t.payload.request));
      for (const [k, v] of fieldsEntries) kv.append(el("dt", null, k), el("dd", null, v));
      requestNodes.push(kv);
    }
    if (!fieldsEntries.length) requestNodes.push(el("p", "hint", "Студент не передал личные данные: в обращение попал только его документ."));
    if (t.has_attachment) {
      const a = el("a", "t-link attach", null, { href: `/api/admin/tickets/${t.id}/attachment` });
      a.append(icon("download"), document.createTextNode("Скачать заявление"));
      requestNodes.push(a);
    }
    body.appendChild(section("Заявка", null, ...requestNodes));

    // история: лента событий, новые сверху; по умолчанию три последних
    const historyBox = el("div");
    let historyItems = t.history || [];
    let showAll = false;
    const drawHistory = (items) => {
      historyItems = items;
      clear(historyBox);
      const ul = el("ul", "timeline");
      for (const h of showAll ? items : items.slice(0, 3)) {
        const li = el("li");
        const text = el("span", "t-text");
        text.append(el("b", null, h.actor === "student" ? "Студент" : (ACTOR_TITLES[h.actor] || h.actor)), document.createTextNode(` · ${h.text}`));
        li.append(text, el("time", null, fmtWhen(h.at)));
        ul.appendChild(li);
      }
      historyBox.appendChild(ul);
      if (items.length > 3) historyBox.appendChild(button(showAll ? "Свернуть" : `Показать ещё ${items.length - 3}`, () => { showAll = !showAll; drawHistory(historyItems); }, "t-link"));
    };
    drawHistory(historyItems);
    const refreshHistory = () => api("GET", `/api/admin/tickets/${t.id}`).then((r) => drawHistory(r.history || [])).catch(() => undefined);

    /** Текстовое поле обращения: кнопка «Сохранить» появляется только при изменении, Ctrl + Enter тоже сохраняет. */
    const savedText = (label, id, value, placeholder, apiField, saveLabel, done, presets = []) => {
      const area = el("textarea", null, null, { id, rows: "4", maxlength: "2000", placeholder });
      area.value = value || "";
      let saved = area.value;
      const actions = el("div", "form-actions hidden");
      const save = button(saveLabel, () => guarded(async () => {
        const res = await api("PATCH", `/api/admin/tickets/${t.id}`, { [apiField]: area.value });
        if (apiField === "reply" && res && !("reply" in res)) throw new Error("Сервер не умеет сохранять ответ студенту: он запущен со старой версией проекта. Остановите сервер, запустите его заново и повторите.");
        saved = area.value;
        actions.classList.add("hidden");
        toast(done);
        load();
        refreshHistory();
      }), "small primary", "check");
      actions.append(save, el("span", "hint", "Ctrl + Enter — сохранить"));
      area.addEventListener("input", () => actions.classList.toggle("hidden", area.value === saved));
      area.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && area.value !== saved) save.click(); });
      const nodes = [el("label", "sr", label, { for: id })];
      if (presets.length) {  // заготовки: нажатие вставляет текст (к уже написанному — с новой строки), его можно править до сохранения
        const row = el("div", "preset-row");
        for (const p of presets) {
          const b = el("button", "chip-btn", p.title, { type: "button", title: p.text });
          b.addEventListener("click", () => {
            area.value = area.value.trim() ? `${area.value.trim()}\n\n${p.text}` : p.text;
            area.dispatchEvent(new Event("input"));
            area.focus();
          });
          row.appendChild(b);
        }
        nodes.push(row);
      }
      nodes.push(area, actions);
      return nodes;
    };
    const replyNodes = savedText("Текст ответа студенту", "t-reply", t.reply, "Студент увидит это на странице «Статус обращения» по своему коду", "reply", "Отправить ответ", `Ответ сохранён. Студент увидит его на странице «Статус обращения» по номеру ${t.id} и своему коду.`, replyPresets);
    body.appendChild(section("Ответ студенту", t.reply_at ? `отправлен ${fmtWhen(t.reply_at)}` : "", ...replyNodes));

    // заметка: если её нет, скрыта за ссылкой — карточка не перегружена
    const noteNodes = savedText("Заметка сотрудника", "t-note", t.note, "Что сделано, кому передано — видно только сотрудникам", "note", "Сохранить заметку", "Заметка сохранена");
    if (t.note) {
      body.appendChild(section("Заметка", "видна только сотрудникам", ...noteNodes));
    } else {
      const holder = el("div", "hidden");
      holder.append(...noteNodes);
      const add = button("Добавить заметку", () => { holder.classList.remove("hidden"); add.classList.add("hidden"); holder.querySelector("textarea").focus(); }, "t-link");
      const sec = section("Заметка", "", add, holder);
      body.appendChild(sec);
    }
    body.appendChild(section("История", "", historyBox));
    rightBox.append(head, body);
  }

  const onFilter = () => { ticketFilter.from = from.value; ticketFilter.to = to.value; ticketFilter.type = type.value; ticketFilter.topic = topic.value; load(); };
  for (const n of [from, to, type, topic]) n.addEventListener("change", onFilter);
  const caseEmpty = el("div", "case-empty");
  caseEmpty.append(icon("inbox"), el("b", null, "Выберите обращение"), el("span", null, "Слева — список; по нему можно переходить стрелками ↑ и ↓. Здесь появятся данные студента, заявление, статус, ответ и история."));
  rightBox.appendChild(caseEmpty);
  await load();
  if (openTicketId && !rows().length) openTicketId = null;
}
