"use strict";

const $ = (id) => document.getElementById(id);
const TABS = [
  ["overview", "Обзор", "chart"],
  ["templates", "Шаблоны DOCX", "doc"],
  ["kb", "База знаний", "book"],
  ["tickets", "Обращения", "inbox"],
  ["history", "История", "history"],
  ["account", "Аккаунт", "key"],
];
let current = "overview";

const STATUS_TITLES = { new: "новое", in_progress: "в работе", done: "обработано", rejected: "отклонено" };
const TYPE_TITLES = { transfer: "перевод", enrollment: "зачисление", other: "другое" };
const OBJECT_TITLES = { template: "шаблон", kb: "база знаний", admin: "учётная запись", doctype: "тип документа", ticket: "обращение", storage: "хранилище" };
// подробности записи журнала: понятные подписи; служебное (контрольные суммы, адреса) не показывается
const DETAIL_TITLES = { name: "название", filename: "файл", version: "версия", clauses: "пунктов", placeholders: "поля", username: "логин", status: "статус", note: "примечание",
  created: "новый", active: "действует", sessions: "сессий", files: "файлов", tickets: "обращений", events: "событий", admin_sessions: "входов администраторов", documents: "документов" };
const HIDDEN_DETAILS = new Set(["sha256", "ip"]);

function humanDetails(details) {
  return Object.entries(details || {}).filter(([k]) => !HIDDEN_DETAILS.has(k)).map(([k, v]) => {
    const value = Array.isArray(v) ? v.join(", ") : typeof v === "boolean" ? (v ? "да" : "нет") : k === "status" ? (STATUS_TITLES[v] || v) : v;
    return `${DETAIL_TITLES[k] || k}: ${value}`;
  }).join("; ");
}
const ACTION_TITLES = {
  "admin.login": "вход администратора", "admin.login_failed": "неудачный вход", "admin.logout": "выход", "admin.create": "создан администратор",
  "admin.password_change": "смена пароля", "template.create": "шаблон добавлен", "template.replace": "шаблон заменён", "template.rename": "шаблон переименован",
  "template.delete": "шаблон удалён", "kb.create": "документ добавлен", "kb.replace": "документ заменён", "kb.update": "документ изменён", "kb.delete": "документ удалён",
  "kb.reset_base": "возврат к файлу Положения", "kb.reindex": "переиндексация", "doctype.save": "тип документа сохранён", "doctype.delete": "тип документа удалён",
  "ticket.update": "обращение обновлено", "storage.purge": "очистка просроченных данных",
};

async function guarded(fn) {
  try {
    return await fn();
  } catch (e) {
    if (e.status === 401) return showLogin("Войдите, чтобы продолжить.");
    showBanner($("banner"), "bad", e.message);
  }
}

function showLogin(note) {
  $("login").classList.remove("hidden");
  $("app").classList.add("hidden");
  $("btn-logout").classList.add("hidden");
  $("who").textContent = "";
  showBanner($("banner"), note ? "warn" : "ok", note || "");
}

// ---------------------------------------------------------------- строительные блоки
function card(title, iconName, ...children) {
  const c = el("section", "card");
  const head = el("div", "card-head");
  const badge = el("span", "card-icon");
  badge.appendChild(icon(iconName));
  head.append(badge, el("h2", null, title));
  c.append(head, ...children);
  return c;
}

function table(headers, rows) {
  const t = el("table");
  const thead = el("thead");
  const head = el("tr");
  for (const h of headers) head.appendChild(el("th", null, h));
  thead.appendChild(head);
  const body = el("tbody");
  rows.forEach((r, i) => {
    const tr = el("tr");
    tr.style.animationDelay = `${Math.min(i * 35, 600)}ms`;
    for (const c of r) {
      const td = el("td");
      if (c instanceof Node) td.appendChild(c);
      else td.textContent = c === null || c === undefined ? "—" : String(c);
      tr.appendChild(td);
    }
    body.appendChild(tr);
  });
  t.append(thead, body);
  return t;
}

function button(text, onClick, cls, iconName) {
  const b = el("button", cls || null, null, { type: "button" });
  if (iconName) b.appendChild(icon(iconName));
  b.appendChild(document.createTextNode(text));
  b.addEventListener("click", onClick);
  return b;
}

function field(labelText, node, id) {
  const wrap = el("div");
  wrap.append(el("label", null, labelText, id ? { for: id } : undefined), node);
  return wrap;
}

// ---------------------------------------------------------------- вкладки со скользящим индикатором
function renderTabs() {
  const tabs = clear($("tabs"));
  const ind = el("span", "tab-ind", null, { "aria-hidden": "true" });
  tabs.appendChild(ind);
  for (const [id, title, ic] of TABS) {
    const b = el("button", null, null, { role: "tab", "aria-selected": String(id === current), type: "button" });
    b.append(icon(ic), document.createTextNode(title));
    b.addEventListener("click", () => {
      if (current === id) return;
      current = id;
      for (const x of tabs.querySelectorAll("button")) x.setAttribute("aria-selected", String(x === b));
      moveIndicator();
      render();
    });
    tabs.appendChild(b);
  }
  requestAnimationFrame(moveIndicator);
}

function moveIndicator() {
  const tabs = $("tabs");
  const active = tabs.querySelector('button[aria-selected="true"]');
  const ind = tabs.querySelector(".tab-ind");
  if (!active || !ind) return;
  ind.style.width = `${active.offsetWidth}px`;
  ind.style.transform = `translateX(${active.offsetLeft}px)`;
}
window.addEventListener("resize", moveIndicator);

async function render() {
  const panel = clear($("panel"));
  const wrap = el("div", "panel");
  panel.appendChild(wrap);
  showBanner($("banner"), "ok", "");
  const views = { overview: viewOverview, templates: viewTemplates, kb: viewKb, tickets: viewTickets, history: viewHistory, account: viewAccount };
  await guarded(() => views[current](wrap));
}

// ---------------------------------------------------------------- обзор и статистика
async function viewOverview(panel) {
  const [info, stats] = await Promise.all([api("GET", "/api/admin/info"), api("GET", "/api/admin/stats?days=30")]);
  const t = stats.totals;
  const kpis = el("div", "kpis");
  [
    [t.visits, "посещений страниц"], [t.questions + t.document_checks, "обращений к секретарю"], [t.screenings_transfer, "анкет на перевод"],
    [t.screenings_enrollment, "анкет на зачисление"], [t.documents_generated, "документов сформировано"], [t.tickets_sent, "обращений в деканат"],
  ].forEach(([value, label], i) => {
    const k = el("div", "kpi");
    const num = el("strong", null, "0");
    k.append(num, el("span", null, label));
    k.style.animationDelay = `${i * 60}ms`;
    kpis.appendChild(k);
    countUp(num, value);
  });
  panel.appendChild(card(`Статистика за ${stats.period_days} дней`, "chart", kpis,
    el("p", "muted", "В статистику попадают только дата, тема, сценарий и итог. Тексты вопросов, OCR и персональные данные не сохраняются.")));

  // популярные темы: одна серия — один цвет, подписи и числа рядом с полосой
  const topicsBody = el("div");
  if (!stats.topics.length) topicsBody.appendChild(el("p", "muted", "Пока нет данных."));
  else {
    const max = Math.max(...stats.topics.map((x) => x.count));
    const ul = el("ul", "bars");
    stats.topics.forEach((x, i) => {
      const li = el("li", null, null, { title: `${x.topic}: ${x.count}` });
      const bar = el("span", "bar");
      const fill = el("i");
      fill.style.width = `${Math.max(2, (x.count / max) * 100)}%`;
      fill.style.animationDelay = `${i * 70}ms`;
      bar.appendChild(fill);
      li.style.animationDelay = `${i * 50}ms`;
      li.append(el("span", null, x.topic), bar, el("span", "num", String(x.count)));
      ul.appendChild(li);
    });
    topicsBody.appendChild(ul);
  }
  // запросы по дням: одна серия (вопросы + проверки документов), подсказка при наведении
  const days = stats.by_day.slice(-21);
  const daysBody = el("div");
  if (!days.length) daysBody.appendChild(el("p", "muted", "Пока нет данных."));
  else {
    const values = days.map((d) => (d.question || 0) + (d.document_check || 0));
    const max = Math.max(1, ...values);
    const cols = el("div", "cols", null, { role: "img", "aria-label": "Запросы по дням" });
    days.forEach((d, i) => {
      const col = el("span", "col", null, { tabindex: "0" });
      const bar = el("i");
      bar.style.height = `${Math.max(2, (values[i] / max) * 100)}%`;
      bar.style.animationDelay = `${i * 30}ms`;
      col.append(bar, el("span", "tip", `${d.day}: ${values[i]} запр., ${d.visit || 0} посещ.`));
      cols.appendChild(col);
    });
    daysBody.append(cols, el("div", "axis"));
    daysBody.lastChild.append(el("span", null, days[0].day), el("span", null, days[days.length - 1].day));
    daysBody.appendChild(table(["День", "Посещения", "Вопросы", "Проверки", "Анкеты", "Документы"], days.slice(-7).reverse().map((d) => [
      d.day, d.visit || 0, d.question || 0, d.document_check || 0, d.screening_started || 0, d.document_generated || 0,
    ])));
  }
  const grid = el("div", "grid");
  grid.append(card("Популярные темы", "spark", topicsBody), card("Запросы по дням", "chart", daysBody));
  panel.appendChild(grid);

  panel.appendChild(card("Сводка", "inbox", table(["Показатель", "Значение"], [
    ["Новых обращений", info.tickets_new],
    ["Шаблонов документов", info.templates],
    ["Документов в базе знаний", info.kb_documents],
    ["Пунктов Положения", info.regulation.clauses],
    ["Отправка обращений из анкеты", info.delivery.available ? "работает" : "сейчас недоступна"],
  ])));
}

// ---------------------------------------------------------------- шаблоны
async function viewTemplates(panel) {
  const items = await api("GET", "/api/admin/templates");
  const nameIn = el("input", null, null, { type: "text", id: "t-name", maxlength: "120" });
  const fileIn = el("input", null, null, { type: "file", id: "t-file", accept: ".docx" });
  const idIn = el("input", null, null, { type: "text", id: "t-id", maxlength: "64", placeholder: "например, perevod-2026" });
  const rep = el("label", "inline");
  const repCb = el("input", null, null, { type: "checkbox" });
  rep.append(repCb, el("span", null, "Заменить существующий шаблон с этим идентификатором (прежняя версия останется в истории)"));
  const upload = button("Загрузить шаблон", () => guarded(async () => {
    const f = fileIn.files[0];
    if (!f) return showBanner($("banner"), "warn", "Выберите файл .docx");
    const form = new FormData();
    form.append("file", f);
    form.append("name", nameIn.value);
    form.append("template_id", idIn.value);
    form.append("replace", repCb.checked ? "true" : "false");
    const info = await api("POST", "/api/admin/templates", form);
    const keys = info.placeholders.map((p) => p.key).join(", ") || "в тексте нет полей вида {{ ключ }}";
    showBanner($("banner"), info.placeholders.length ? "ok" : "warn", `Шаблон «${info.name}» сохранён (версия ${info.version}). Поля для заполнения: ${keys}.`);
    render();
  }), "primary", "upload");
  upload.style.marginTop = "14px";
  panel.appendChild(card("Добавить шаблон", "upload",
    el("p", "muted", "Файл .docx с оформлением, шапкой и подписями. Переменные: {{ student_name }}, {{ passport_data }}, {{ current_university }}, {{ current_course }}, {{ current_specialty }}, {{ target_specialty }}, {{ education_form }}, {{ debts }}, {{ application_date }}; можно добавлять и свои."),
    field("Название (необязательно)", nameIn, "t-name"), field("Файл .docx", fileIn, "t-file"),
    field("Идентификатор (нужен, чтобы заменить существующий шаблон)", idIn, "t-id"), rep, upload));

  const list = items.length ? table(["Название", "Идентификатор", "Версия", "Ключи", "Обновлён", ""], items.map((t) => {
    const actions = el("span", "row");
    const dl = el("a", "btn small", null, { href: `/api/admin/templates/${encodeURIComponent(t.id)}/download` });
    dl.append(icon("download"), document.createTextNode("Скачать"));
    actions.append(
      dl,
      button("История", () => guarded(async () => showHistory("template", t.id)), "small", "history"),
      button("Удалить", () => guarded(async () => {
        if (!confirm(`Удалить шаблон «${t.name}» со всеми версиями? Пользователи, не завершившие создание документа, получат сообщение, что шаблона больше нет.`)) return;
        await api("DELETE", `/api/admin/templates/${encodeURIComponent(t.id)}`);
        render();
      }), "small danger", "trash"),
    );
    return [t.name, t.id, t.version, t.placeholders.map((p) => p.key).join(", "), fmtDate(t.updated_at), actions];
  })) : el("p", "muted", "Шаблонов нет. Пока их нет, пользователи не смогут сформировать документ.");
  panel.appendChild(card("Шаблоны", "doc", list));
  panel.appendChild(el("div", null, null, { id: "history-box" }));
}

async function showHistory(type, id) {
  const path = type === "template" ? `/api/admin/templates/${encodeURIComponent(id)}/history` : `/api/admin/kb/${encodeURIComponent(id)}/history`;
  const h = await api("GET", path);
  const box = clear($("history-box"));
  const c = card(`История: ${id}`, "history",
    table(["Версия", "Дата", "Кто", type === "kb" ? "Пунктов" : "Ключи"], h.versions.map((v) => [v.version, fmtDate(v.created_at), v.created_by, type === "kb" ? v.clauses : (v.placeholders || []).join(", ")])),
    el("p", "eyebrow", "Журнал действий"),
    table(["Когда", "Кто", "Действие"], h.log.map((l) => [fmtDate(l.ts), l.actor, ACTION_TITLES[l.action] || l.action])));
  box.appendChild(c);
  c.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" });
}

// ---------------------------------------------------------------- база знаний
async function viewKb(panel) {
  const items = await api("GET", "/api/admin/kb");
  const nameIn = el("input", null, null, { type: "text", id: "k-name", maxlength: "120" });
  const fileIn = el("input", null, null, { type: "file", id: "k-file", accept: ".txt,.md,.docx,.pdf" });
  const row = el("div", "row");
  row.style.marginTop = "14px";
  row.append(
    button("Загрузить", () => guarded(async () => {
      const f = fileIn.files[0];
      if (!f) return showBanner($("banner"), "warn", "Выберите файл");
      const form = new FormData();
      form.append("file", f);
      form.append("name", nameIn.value);
      const r = await api("POST", "/api/admin/kb", form);
      showBanner($("banner"), "ok", `Документ «${r.name}» добавлен: ${r.clauses} пунктов. Индекс обновлён.`);
      render();
    }), "primary", "upload"),
    button("Обновить индекс", () => guarded(async () => {
      const r = await api("POST", "/api/admin/reindex");
      showBanner($("banner"), "ok", `Индекс пересобран: ${r.clauses} пунктов из ${r.documents} документов.`);
    }), null, "spark"),
  );
  panel.appendChild(card("Загрузить документ с регламентом", "upload",
    el("p", "muted", ".txt, .md, .docx или .pdf с текстовым слоем. Нумерация пунктов («3.4.», «п. 3.4») определяется автоматически, индекс обновляется сразу."),
    field("Название документа", nameIn, "k-name"), field("Файл", fileIn, "k-file"), row));

  panel.appendChild(card("Документы базы знаний", "book", table(["Название", "Тип", "Пунктов", "Версия", "Статус", "Обновлён", ""], items.map((d) => {
    const act = el("span", "row");
    const replaceInput = el("input", null, null, { type: "file", accept: ".txt,.md,.docx,.pdf", "aria-label": `Заменить ${d.name}` });
    replaceInput.addEventListener("change", () => guarded(async () => {
      const f = replaceInput.files[0];
      if (!f) return;
      const form = new FormData();
      form.append("file", f);
      const r = await api("PUT", `/api/admin/kb/${encodeURIComponent(d.id)}`, form);
      showBanner($("banner"), "ok", `Документ заменён (версия ${r.version}, пунктов: ${r.clauses}). Прежняя версия сохранена в истории.`);
      render();
    }));
    act.append(
      replaceInput,
      button(d.active ? "Отключить" : "Включить", () => guarded(async () => { await api("PATCH", `/api/admin/kb/${encodeURIComponent(d.id)}`, { active: !d.active }); render(); }), "small"),
      button("История", () => guarded(async () => showHistory("kb", d.id)), "small", "history"),
      button(d.kind === "base" ? "Вернуть файл" : "Удалить", () => guarded(async () => {
        const msg = d.kind === "base" ? "Вернуть основное Положение из файла в настройках? Загруженная замена будет убрана из действующих правил (в истории останется)." : `Удалить «${d.name}»? Его пункты перестанут использоваться в ответах.`;
        if (!confirm(msg)) return;
        await api("DELETE", `/api/admin/kb/${encodeURIComponent(d.id)}`);
        render();
      }), "small danger", d.kind === "base" ? "history" : "trash"),
    );
    const status = el("span", `chip ${d.active ? "" : "miss"}`, d.active ? "действует" : "отключён");
    return [d.name, d.kind === "base" ? "основное Положение" : "дополнительный", d.clauses, d.version || "файл", status, fmtDate(d.updated_at), act];
  }))));
  panel.appendChild(el("div", null, null, { id: "history-box" }));
}

// ---------------------------------------------------------------- обращения
async function viewTickets(panel) {
  const filters = el("div", "row");
  const mk = (label, node) => {
    const w = el("div", "grow");
    w.append(el("label", null, label), node);
    filters.appendChild(w);
    return node;
  };
  const from = mk("С даты", el("input", null, null, { type: "date" }));
  const to = mk("По дату", el("input", null, null, { type: "date" }));
  const type = mk("Тип", el("select"));
  const status = mk("Статус", el("select"));
  const topic = mk("Тема", el("select"));
  const out = el("div");
  const show = button("Показать", () => load(), "primary", "inbox");
  show.style.marginTop = "14px";
  panel.appendChild(card("Обращения по переводу и зачислению", "inbox", filters, show, out));
  panel.appendChild(el("div", null, null, { id: "ticket-box" }));

  const fill = (sel, values, titles) => {
    sel.appendChild(el("option", null, "все", { value: "" }));
    for (const v of values) sel.appendChild(el("option", null, titles ? titles[v] || v : v, { value: v }));
  };

  async function load() {
    const q = new URLSearchParams({ date_from: from.value, date_to: to.value, type: type.value, status: status.value, topic: topic.value });
    const res = await guarded(() => api("GET", "/api/admin/tickets?" + q));
    if (!res) return;
    if (!type.options.length) {
      fill(type, res.types, TYPE_TITLES);
      fill(status, res.statuses, STATUS_TITLES);
      fill(topic, res.topics);
    }
    clear(out);
    out.appendChild(el("p", "muted", `Найдено: ${res.total}`));
    if (res.items.length) {
      out.appendChild(table(["№", "Создано", "Тип", "Тема", "Статус", ""], res.items.map((t) => [t.id, fmtDate(t.created_at), TYPE_TITLES[t.type] || t.type, t.topic,
        el("span", `chip ${t.status === "new" ? "src" : ""}`, STATUS_TITLES[t.status] || t.status), button("Открыть", () => guarded(() => openTicket(t.id)), "small")])));
    }
  }

  async function openTicket(id) {
    const t = await api("GET", `/api/admin/tickets/${id}`);
    const box = clear($("ticket-box"));
    const st = el("select", null, null, { id: "t-status" });
    for (const s of Object.keys(STATUS_TITLES)) {
      const o = el("option", null, STATUS_TITLES[s], { value: s });
      if (s === t.status) o.selected = true;
      st.appendChild(o);
    }
    const note = el("textarea", null, null, { id: "t-note", rows: "3", maxlength: "2000" });
    note.value = t.note || "";
    const parts = [el("p", "muted", `${TYPE_TITLES[t.type] || t.type} · ${t.topic || "—"} · ${fmtDate(t.created_at)}`), table(["Поле", "Значение"], Object.entries((t.payload || {}).fields || {}))];
    if (t.has_attachment) {
      const a = el("a", "btn small", null, { href: `/api/admin/tickets/${t.id}/attachment` });
      a.append(icon("download"), document.createTextNode("Скачать приложенный документ"));
      parts.push(a);
    }
    const save = button("Сохранить", () => guarded(async () => {
      await api("PATCH", `/api/admin/tickets/${t.id}`, { status: st.value, note: note.value });
      showBanner($("banner"), "ok", "Сохранено.");
      load();
    }), "primary", "check");
    save.style.marginTop = "14px";
    parts.push(field("Статус", st, "t-status"), field("Заметка сотрудника", note, "t-note"), save);
    const c = card(`Обращение № ${t.id}`, "inbox", ...parts);
    box.appendChild(c);
    c.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" });
  }
  load();
}

// ---------------------------------------------------------------- журнал
async function viewHistory(panel) {
  const log = await api("GET", "/api/admin/audit?limit=200");
  const purge = button("Удалить просроченные данные сейчас", () => guarded(async () => {
    const r = await api("POST", "/api/admin/purge");
    showBanner($("banner"), "ok", "Удалено по сроку хранения — " + humanDetails(r) + ".");
  }), null, "trash");
  purge.style.marginTop = "14px";
  panel.appendChild(card("История изменений административных документов", "history",
    el("p", "muted", "Шаблоны, документы базы знаний, типы документов, учётные записи. Содержимое паролей и документов в журнал не попадает."),
    table(["Когда", "Кто", "Действие", "Объект", "Подробности"], log.map((l) => [
      fmtDate(l.ts), l.actor, ACTION_TITLES[l.action] || l.action, `${OBJECT_TITLES[l.object_type] || l.object_type}${l.object_id ? " «" + l.object_id + "»" : ""}`,
      humanDetails(l.details),
    ])), purge));
}

// ---------------------------------------------------------------- аккаунт
async function viewAccount(panel) {
  const cur = el("input", null, null, { type: "password", id: "pw-cur", autocomplete: "current-password" });
  const nw = el("input", null, null, { type: "password", id: "pw-new", autocomplete: "new-password" });
  const btn = button("Сменить пароль", () => guarded(async () => {
    await api("POST", "/api/admin/password", { current: cur.value, new: nw.value });
    cur.value = "";
    nw.value = "";
    showBanner($("banner"), "ok", "Пароль изменён.");
  }), "primary", "key");
  btn.style.marginTop = "14px";
  panel.appendChild(card("Смена пароля", "key", field("Текущий пароль", cur, "pw-cur"), field("Новый пароль", nw, "pw-new"),
    el("p", "muted", "Не короче 10 символов, не из простых. После смены остальные ваши входы завершаются."), btn));
}

// ---------------------------------------------------------------- вход / выход
async function start() {
  try {
    const me = await api("GET", "/api/admin/me");
    $("who").textContent = me.username;
    $("login").classList.add("hidden");
    $("app").classList.remove("hidden");
    $("btn-logout").classList.remove("hidden");
    renderTabs();
    render();
  } catch (e) {
    if (e.status === 401) showLogin("");
    else showBanner($("banner"), "bad", e.message);
  }
}

$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await api("POST", "/api/admin/login", { username: $("u").value, password: $("p").value });
    $("p").value = "";
    start();
  } catch (e) {
    showBanner($("banner"), "bad", e.message);
    $("login").animate && !REDUCED && $("login").animate([{ transform: "translateX(-6px)" }, { transform: "translateX(6px)" }, { transform: "none" }], { duration: 320 });
  }
});
$("btn-logout").prepend(icon("logout"));
$("btn-logout").addEventListener("click", async () => {
  await api("POST", "/api/admin/logout").catch(() => null);
  showLogin("Вы вышли.");
});

start();
