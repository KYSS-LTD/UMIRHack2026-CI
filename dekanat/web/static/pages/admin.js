/* Кабинет сотрудника (/admin). Весь текст из данных выводится через textContent; стили — классами или через CSSOM (строгий CSP). */
"use strict";

const $ = (id) => document.getElementById(id);

const TABS = [
  ["overview", "Обзор"],
  ["tickets", "Обращения"],
  ["templates", "Шаблоны"],
  ["kb", "База знаний"],
  ["history", "История"],
  ["account", "Аккаунт"],
];
let current = (TABS.find(([id]) => location.hash === `#${id}`) || TABS[0])[0];
let period = 30; // дней в статистике
const counts = {}; // счётчики на закладках: tickets (новые), templates, kb
const ticketFilter = { from: "", to: "", type: "", status: "", topic: "" };
let openTicketId = null;

const STATUS_TITLES = { new: "новое", in_progress: "в работе", done: "обработано", rejected: "отклонено" };
const STATUS_PLURAL = { new: "Новые", in_progress: "В работе", done: "Обработаны", rejected: "Отклонены" };
const STATUS_LABEL = { new: "Новое", in_progress: "В работе", done: "Обработано", rejected: "Отклонено" };
const TYPE_TITLES = { transfer: "Перевод", enrollment: "Зачисление", other: "Другое" };
const OBJECT_TITLES = { template: "шаблон", kb: "документ базы знаний", admin: "учётная запись", doctype: "тип документа", ticket: "обращение", storage: "хранилище" };
const FIELD_TITLES = {
  student_name: "ФИО", passport_data: "Паспортные данные", current_university: "Текущий вуз", current_course: "Текущий курс", current_specialty: "Направление (текущее)",
  target_specialty: "Целевое направление", education_form: "Форма обучения", debts: "Академические задолженности", application_date: "Дата заявления",
};
const ACTION_TITLES = {
  "admin.login": "вошёл(а) в кабинет", "admin.login_failed": "неудачная попытка входа", "admin.logout": "вышел(а) из кабинета", "admin.create": "создана учётная запись",
  "admin.password_change": "сменил(а) пароль", "template.create": "добавлен шаблон", "template.replace": "заменён шаблон", "template.rename": "переименован шаблон",
  "template.delete": "удалён шаблон", "kb.create": "добавлен документ", "kb.replace": "заменён документ", "kb.update": "изменён документ", "kb.delete": "удалён документ",
  "kb.reset_base": "возвращено Положение из файла", "kb.reindex": "обновлён поиск по документам", "doctype.save": "сохранён тип документа", "doctype.delete": "удалён тип документа",
  "ticket.update": "обновлено обращение", "storage.purge": "удалены просроченные данные",
};
// подробности записи журнала: понятные подписи; служебное (контрольные суммы, адреса) не показывается
const DETAIL_TITLES = {
  name: "название", filename: "файл", version: "версия", clauses: "пунктов", placeholders: "данные", username: "логин", status: "статус", note: "заметка",
  created: "новый", active: "действует", sessions: "сессий", files: "файлов", tickets: "обращений", events: "событий", admin_sessions: "входов", documents: "документов",
};
const HIDDEN_DETAILS = new Set(["sha256", "ip"]);

function humanDetails(details) {
  return Object.entries(details || {})
    .filter(([k, v]) => !HIDDEN_DETAILS.has(k) && v !== null && v !== undefined && v !== "" && !(k === "note" && v === false))
    .map(([k, v]) => {
      if (k === "note") return "заметка обновлена";
      const value = Array.isArray(v) ? v.map((x) => keyTitle(x)).join(", ") : typeof v === "boolean" ? (v ? "да" : "нет") : k === "status" ? (STATUS_TITLES[v] || v) : v;
      return `${DETAIL_TITLES[k] || k}: ${value}`;
    }).join(" · ");
}
// кто сделал: служебные имена — словами
const ACTOR_TITLES = { cli: "администратор сервера", system: "система" };

/** Название данных в шаблоне: известные — по-русски, свои метки — без подчёркиваний. */
const keyTitle = (k) => FIELD_TITLES[k] || String(k).replace(/_/g, " ");

const fmtSize = (n) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} КБ` : `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} МБ`);

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
  $("who").classList.add("hidden");
  showBanner($("banner"), note ? "warn" : "ok", note || "");
}

let toastTimer = 0;
function toast(text) {
  let t = document.querySelector(".toast");
  if (!t) {
    t = el("div", "toast", null, { role: "status" });
    document.body.appendChild(t);
  }
  t.textContent = text;
  t.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("on"), 2200);
}

// ---------------------------------------------------------------- строительные блоки
/** Лист — раздел страницы: заголовок с линейкой, справа короткая пометка. */
function sheet(title, aside, ...children) {
  const s = el("section", "sheet");
  const head = el("div", "sheet-head");
  head.appendChild(el("h2", null, title));
  if (aside instanceof Node) head.appendChild(aside);
  else if (aside) head.appendChild(el("span", "aside", aside));
  s.append(head, ...children);
  return s;
}

function table(headers, rows, numeric = []) {
  const t = el("table");
  const head = el("tr");
  headers.forEach((h, i) => head.appendChild(el("th", numeric.includes(i) ? "num" : null, h)));
  t.appendChild(el("thead")).appendChild(head);
  const body = el("tbody");
  for (const r of rows) {
    const tr = el("tr");
    r.forEach((c, i) => {
      const td = el("td", numeric.includes(i) ? "num" : null);
      if (c instanceof Node) td.appendChild(c);
      else td.textContent = c === null || c === undefined ? "—" : String(c);
      tr.appendChild(td);
    });
    body.appendChild(tr);
  }
  t.appendChild(body);
  const wrap = el("div", "table-wrap");
  wrap.appendChild(t);
  return wrap;
}

function button(text, onClick, cls, iconName) {
  const b = el("button", cls || null, null, { type: "button" });
  if (iconName) b.appendChild(icon(iconName));
  b.appendChild(document.createTextNode(text));
  b.addEventListener("click", onClick);
  return b;
}

function field(labelText, node, id, hint) {
  const wrap = el("div");
  const l = el("label", null, labelText, id ? { for: id } : undefined);
  if (hint) l.appendChild(el("small", null, ` ${hint}`));
  wrap.append(l, node);
  return wrap;
}

function seg(options, value, onPick, label) {
  const box = el("div", "seg", null, { role: "group", "aria-label": label });
  for (const [v, text] of options) {
    const b = el("button", null, text, { type: "button", "aria-pressed": String(v === value) });
    b.addEventListener("click", () => onPick(v));
    box.appendChild(b);
  }
  return box;
}

/** Меню «⋯»: редкие действия прячутся сюда, чтобы строка с шаблоном или документом не разрасталась. */
function moreMenu(label, items) {
  const wrap = el("div", "more");
  const btn = el("button", "more-btn ghost small", "⋯", { type: "button", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": label, title: label });
  const list = el("div", "more-list", null, { role: "menu" });
  const onDown = (e) => { if (!wrap.contains(e.target)) close(); };
  const onKey = (e) => { if (e.key === "Escape") { close(); btn.focus(); } };
  function close() {
    list.classList.remove("on");
    btn.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", onDown, true);
    document.removeEventListener("keydown", onKey);
  }
  for (const it of items) {
    const b = el("button", `more-item${it.danger ? " danger" : ""}`, null, { type: "button", role: "menuitem" });
    if (it.icon) b.appendChild(icon(it.icon));
    b.appendChild(document.createTextNode(it.text));
    b.addEventListener("click", () => { close(); it.run(); });
    list.appendChild(b);
  }
  btn.addEventListener("click", () => {
    if (list.classList.contains("on")) return close();
    list.classList.add("on");
    btn.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey);
    list.querySelector("button").focus();
  });
  wrap.append(btn, list);
  return wrap;
}

/** Подтверждение в самой странице (вместо системного окна): Esc и клик мимо отменяют. */
function askConfirm({ title, text, ok = "Удалить", danger = true }) {
  return new Promise((resolve) => {
    const dlg = el("dialog", "confirm");
    const body = el("div", "confirm-body");
    const actions = el("div", "form-actions");
    const cancel = button("Отмена", () => dlg.close("cancel"), "ghost");
    actions.append(cancel, button(ok, () => dlg.close("ok"), danger ? "danger solid" : "primary"));
    body.append(el("h3", null, title), el("p", null, text), actions);
    dlg.appendChild(body);
    dlg.addEventListener("close", () => { resolve(dlg.returnValue === "ok"); dlg.remove(); });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close("cancel"); });
    document.body.appendChild(dlg);
    dlg.showModal();
    cancel.focus();
  });
}

/** Переключатель «включено / выключено» — вместо пары кнопок «Отключить» и «Включить». */
function toggle(checked, label, onChange) {
  const l = el("label", "switch");
  const input = el("input", null, null, { type: "checkbox", role: "switch" });
  input.checked = checked;
  l.append(input, el("i", null, null, { "aria-hidden": "true" }), el("span", null, label));
  input.addEventListener("change", () => onChange(input.checked));
  return l;
}

function empty(title, text) {
  const d = el("div", "empty");
  d.append(el("b", null, title), document.createTextNode(text));
  return d;
}

/** Выбор файла: перетащить или нажать; выбранный файл — с названием и размером. */
function filePick(id, accept, hint, onChange) {
  const label = el("label", "filepick");
  const input = el("input", null, null, { type: "file", id, accept });
  const badge = el("span", "fp-icon", null, { "aria-hidden": "true" });
  const title = el("strong");
  const sub = el("small");
  label.append(input, badge, title, sub);
  const show = () => {
    const f = input.files && input.files[0];
    label.classList.toggle("chosen", !!f);
    clear(badge).appendChild(icon(f ? "check" : "upload"));
    title.textContent = f ? f.name : "Перетащите файл сюда или нажмите";
    sub.textContent = f ? fmtSize(f.size) : hint;
    if (onChange) onChange(f || null);
  };
  input.addEventListener("change", show);
  for (const ev of ["dragenter", "dragover"]) label.addEventListener(ev, (e) => { e.preventDefault(); label.classList.add("over"); });
  for (const ev of ["dragleave", "drop"]) label.addEventListener(ev, () => label.classList.remove("over"));
  label.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer && e.dataTransfer.files.length) {
      input.files = e.dataTransfer.files;
      show();
    }
  });
  show();
  return { node: label, input, reset() { input.value = ""; show(); } };
}

// ---------------------------------------------------------------- закладки со счётчиками и чернильной чертой
function renderTabs() {
  const tabs = clear($("tabs"));
  tabs.appendChild(el("span", "tab-ind", null, { "aria-hidden": "true" }));
  TABS.forEach(([id, title], i) => {
    const b = el("button", null, null, { role: "tab", "aria-selected": String(id === current), tabindex: id === current ? "0" : "-1", type: "button", "data-tab": id });
    b.style.animationDelay = `${i * 50}ms`;
    b.appendChild(el("span", null, title));
    const n = counts[id];
    if (n) b.appendChild(el("em", `count${id === "tickets" ? " hot" : ""}`, String(n)));
    b.addEventListener("click", () => selectTab(id));
    b.addEventListener("keydown", (e) => {
      const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!dir) return;
      e.preventDefault();
      const next = TABS[(i + dir + TABS.length) % TABS.length][0];
      selectTab(next);
      tabs.querySelector(`[data-tab="${next}"]`).focus();
    });
    tabs.appendChild(b);
  });
  requestAnimationFrame(moveIndicator);
}

function selectTab(id) {
  if (current === id) return;
  current = id;
  try { history.replaceState(null, "", `#${id}`); } catch (e) { /* не критично */ }
  for (const x of $("tabs").querySelectorAll("button")) {
    const on = x.dataset.tab === id;
    x.setAttribute("aria-selected", String(on));
    x.setAttribute("tabindex", on ? "0" : "-1");
  }
  moveIndicator();
  render();
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

async function refreshCounts() {
  const info = await api("GET", "/api/admin/info");
  counts.tickets = info.tickets_new;
  counts.templates = info.templates;
  counts.kb = info.kb_documents;
  for (const [id, n] of Object.entries({ tickets: info.tickets_new, templates: info.templates, kb: info.kb_documents })) {
    const b = $("tabs").querySelector(`[data-tab="${id}"]`);
    if (!b) continue;
    const old = b.querySelector(".count");
    if (old) old.remove();
    if (n) b.appendChild(el("em", `count${id === "tickets" ? " hot" : ""}`, String(n)));
  }
  moveIndicator();
  renderSummary(info);
  return info;
}

function renderSummary(info) {
  const box = clear($("summary"));
  if (info.tickets_new > 0) {
    const link = el("button", "link-btn", `${info.tickets_new} ${plural(info.tickets_new, ["новое обращение", "новых обращения", "новых обращений"])}`, { type: "button" });
    link.addEventListener("click", () => { ticketFilter.status = "new"; selectTab("tickets"); render(); });
    box.append("Ждут ответа: ", link, ". ");
  } else {
    box.append("Новых обращений нет — всё разобрано. ");
  }
  if (!info.delivery.available) box.append("Отправка обращений из анкеты сейчас недоступна.");
}

let renderToken = 0;

/**
 * Смена закладки: прежнее содержимое гаснет (140 мс), высота области удерживается, пока строится новое, затем плавно отпускается —
 * страница не «схлопывается» и не прыгает. Быстрые переключения не мешают друг другу: действует только последний вызов.
 */
async function render() {
  const token = ++renderToken;
  const host = $("panel");
  const old = host.firstElementChild;
  if (old) host.style.minHeight = `${host.offsetHeight}px`;
  if (old && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    old.classList.add("leaving");
    await new Promise((r) => setTimeout(r, 140));
    if (token !== renderToken) return;
  }
  const wrap = el("div", "panel");
  clear(host).appendChild(wrap);
  showBanner($("banner"), "ok", "");
  const views = { overview: viewOverview, tickets: viewTickets, templates: viewTemplates, kb: viewKb, history: viewHistory, account: viewAccount };
  await guarded(() => views[current](wrap));
  if (token === renderToken) {
    host.style.minHeight = "0px"; // высота отпускается плавно (transition у #panel)
    setTimeout(() => { if (token === renderToken) host.style.minHeight = ""; }, 360);
  }
}

// ---------------------------------------------------------------- обзор
const lastDays = (n) => Array.from({ length: n }, (_, i) => new Date(Date.now() - (n - 1 - i) * 86400000).toISOString().slice(0, 10));

/** Круглая шкала: максимум округляется вверх до «красивого» числа, не больше четырёх делений. */
function niceScale(max) {
  const m = Math.max(1, max);
  const mag = Math.pow(10, Math.floor(Math.log10(m)));
  let step = mag;
  for (const s of [1, 2, 2.5, 5, 10]) {
    step = s * mag;
    if (m / step <= 4) break;
  }
  const top = Math.ceil(m / step) * step;
  const ticks = [];
  for (let v = 0; v <= top + 1e-9; v += step) ticks.push(Math.round(v * 100) / 100);
  return { top, ticks };
}

function sparkline(values) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 100 30");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "spark");
  svg.setAttribute("aria-hidden", "true");
  const max = Math.max(1, ...values);
  const pts = values.map((v, i) => [values.length === 1 ? 50 : (i / (values.length - 1)) * 100, 28 - (v / max) * 24]);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");
  const area = document.createElementNS(ns, "path");
  area.setAttribute("class", "area");
  area.setAttribute("d", `${line}L100 30L0 30Z`);
  const stroke = document.createElementNS(ns, "path");
  stroke.setAttribute("class", "line");
  stroke.setAttribute("d", line);
  const end = document.createElementNS(ns, "path");
  end.setAttribute("class", "end");
  const [ex, ey] = pts[pts.length - 1];
  end.setAttribute("d", `M${ex.toFixed(1)} ${ey.toFixed(1)}h0`);
  svg.append(area, stroke, end);
  return svg;
}

function statTile(label, value, i, extra) {
  const k = el("div", "stat");
  k.style.animationDelay = `${i * 60}ms`;
  const num = el("strong", "stat-value", "0");
  k.append(el("span", "stat-label", label), num);
  if (extra && extra.delta) k.appendChild(el("span", `stat-delta ${extra.delta.cls}`, extra.delta.text));
  if (extra && extra.series) k.appendChild(sparkline(extra.series));
  countUp(num, value);
  return k;
}

function deltaOf(cur, prev) {
  if (prev > 0) {
    const pct = Math.round(((cur - prev) / prev) * 100);
    return { cls: pct > 0 ? "up" : pct < 0 ? "down" : "", text: `${pct > 0 ? "+" : ""}${pct} % к прошлому периоду` };
  }
  return cur > 0 ? { cls: "", text: "раньше не было" } : null;
}

function dayChart(days, byDay) {
  const rows = days.map((d) => byDay.get(d) || {});
  const values = rows.map((r) => (r.question || 0) + (r.document_check || 0));
  const { top, ticks } = niceScale(Math.max(...values));
  const today = days[days.length - 1];
  const chart = el("div", "daychart");
  const y = el("div", "dc-y", null, { "aria-hidden": "true" });
  for (const t of ticks) {
    const s = el("span", null, fmtNum(t));
    s.style.bottom = `${(t / top) * 100}%`;
    y.appendChild(s);
  }
  const plot = el("div", "dc-plot");
  for (const t of ticks.slice(1)) {
    const g = el("i", "gridline", null, { "aria-hidden": "true" });
    g.style.bottom = `${(t / top) * 100}%`;
    plot.appendChild(g);
  }
  const tip = el("div", "dc-tip", null, { role: "status" });
  const show = (i, col) => {
    const r = rows[i];
    clear(tip);
    tip.appendChild(el("strong", null, fmtDay(days[i], true)));
    const dl = el("dl");
    const add = (k, v, total) => dl.append(el("dt", total ? "total" : null, k), el("dd", total ? "total" : null, fmtNum(v)));
    add("Вопросы секретарю", r.question || 0);
    add("Проверки документов", r.document_check || 0);
    add("Начато анкет", r.screening_started || 0);
    add("Посещения", r.visit || 0);
    add("Запросов всего", values[i], true);
    tip.appendChild(dl);
    tip.classList.add("on");
    const w = tip.offsetWidth;
    const center = col.offsetLeft + col.offsetWidth / 2;
    const wanted = center < plot.clientWidth / 2 ? center + 16 : center - w - 16;
    tip.style.left = `${Math.min(Math.max(0, wanted), Math.max(0, plot.clientWidth - w))}px`;
    tip.style.top = "6px";
  };
  days.forEach((d, i) => {
    const col = el("button", `dc-col${d === today ? " today" : ""}${values[i] ? "" : " zero"}`, null, {
      type: "button", "aria-label": `${fmtDay(d)}: ${values[i]} ${plural(values[i], ["запрос", "запроса", "запросов"])}`,
    });
    const bar = el("i");
    bar.style.height = `${(values[i] / top) * 100}%`;
    bar.style.animationDelay = `${Math.min(i * 14, 600)}ms`;
    col.appendChild(bar);
    col.addEventListener("pointerenter", () => show(i, col));
    col.addEventListener("focus", () => show(i, col));
    col.addEventListener("pointerleave", () => tip.classList.remove("on"));
    col.addEventListener("blur", () => tip.classList.remove("on"));
    plot.appendChild(col);
  });
  plot.appendChild(tip);
  const x = el("div", "dc-x", null, { "aria-hidden": "true" });
  for (const i of new Set([0, Math.floor((days.length - 1) / 2), days.length - 1])) {
    const s = el("span", i === days.length - 1 ? "today" : null, i === days.length - 1 ? "сегодня" : fmtDay(days[i]));
    s.style.left = `${((i + 0.5) / days.length) * 100}%`;
    x.appendChild(s);
  }
  chart.append(y, plot, x);
  return { node: chart, values, rows };
}

async function viewOverview(panel) {
  const days = period;
  const [info, stats, wide] = await Promise.all([
    api("GET", "/api/admin/info"),
    api("GET", `/api/admin/stats?days=${days}`),
    api("GET", `/api/admin/stats?days=${Math.min(365, days * 2)}`),
  ]);
  renderSummary(info);
  const t = stats.totals;
  const byDay = new Map(wide.by_day.map((d) => [d.day, d]));
  const cur = lastDays(days);
  const prev = lastDays(days * 2).slice(0, days);
  const series = (pick) => cur.map((d) => pick(byDay.get(d) || {}));
  const sum = (list, pick) => list.reduce((s, d) => s + pick(byDay.get(d) || {}), 0);
  const pickers = [
    ["Посещения", (d) => d.visit || 0],
    ["Вопросы и проверки", (d) => (d.question || 0) + (d.document_check || 0)],
    ["Начато анкет", (d) => d.screening_started || 0],
    ["Документов сформировано", (d) => d.document_generated || 0],
  ];
  const strip = el("div", "stats-strip");
  pickers.forEach(([label, pick], i) => strip.appendChild(statTile(label, sum(cur, pick), i, { series: series(pick), delta: deltaOf(sum(cur, pick), sum(prev, pick)) })));
  strip.appendChild(statTile("Передано в деканат", t.tickets_sent, pickers.length));
  panel.appendChild(sheet("Работа секретаря", seg([[7, "7 дней"], [30, "30 дней"], [90, "90 дней"]], days, (v) => { period = v; render(); }, "Период"), strip,
    el("p", "sheet-note", "В статистику попадают только дата, тема, сценарий и итог. Тексты вопросов, распознанный текст и персональные данные не сохраняются.")));

  // ---- обращения по дням
  const chartDays = lastDays(Math.min(days, 45));
  const chart = dayChart(chartDays, byDay);
  const total = chart.values.reduce((a, b) => a + b, 0);
  const peak = Math.max(...chart.values);
  const peakDay = chartDays[chart.values.indexOf(peak)];
  const foot = el("div", "chart-foot");
  const key = el("span", "legend-key");
  key.append(el("i"), "вопросы и проверки документов", el("i", "today"), "сегодня");
  const stat = el("span");
  stat.append("Всего ", el("b", null, fmtNum(total)), total ? ` · в среднем ${(total / chartDays.length).toFixed(1).replace(".", ",")} в день · больше всего — ${fmtDay(peakDay)} (${peak})` : "");
  foot.append(key, stat);
  const holder = el("div");
  const asTable = el("div");
  const drawMode = (mode) => {
    clear(asTable).appendChild(seg([["chart", "График"], ["table", "Таблица"]], mode, drawMode, "Вид"));
    clear(holder).appendChild(mode === "table" ? table(["День", "Вопросы", "Проверки", "Анкеты", "Посещения"], chartDays.slice().reverse().map((d) => {
      const r = byDay.get(d) || {};
      return [fmtDay(d, true), r.question || 0, r.document_check || 0, r.screening_started || 0, r.visit || 0];
    }), [1, 2, 3, 4]) : chart.node);
  };
  drawMode("chart");
  const daysSheet = sheet("Обращения по дням", total ? asTable : null, total ? holder : empty("Пока тихо", "За выбранный период обращений не было."), total ? foot : document.createComment(""));

  // ---- темы и исходы
  const topicsBody = el("div");
  if (!stats.topics.length) topicsBody.appendChild(empty("Пока нет данных", "Темы появятся, как только студенты начнут задавать вопросы."));
  else {
    const max = Math.max(...stats.topics.map((x) => x.count));
    const all = stats.topics.reduce((s, x) => s + x.count, 0);
    const ul = el("ul", "topics");
    stats.topics.slice(0, 8).forEach((x, i) => {
      const li = el("li");
      li.style.animationDelay = `${i * 50}ms`;
      const bar = el("span", "t-bar");
      const fill = el("i");
      fill.style.width = `${Math.max(3, (x.count / max) * 100)}%`;
      fill.style.animationDelay = `${i * 70}ms`;
      bar.appendChild(fill);
      li.append(el("span", "t-name", x.topic), el("span", "t-num", fmtNum(x.count)), el("span", "t-share", `${Math.round((x.count / all) * 100)} %`), bar);
      ul.appendChild(li);
    });
    topicsBody.appendChild(ul);
  }

  const outcomes = [["ok", "Ответ найден", "✓"], ["missing", "Чего-то не хватало", "!"], ["no_answer", "Ответа не было", "?"]];
  const answered = outcomes.reduce((s, [k]) => s + (stats.statuses[k] || 0), 0);
  const outBody = el("div");
  if (!answered) outBody.appendChild(empty("Пока нет данных", "Здесь будет видно, как заканчивались ответы."));
  else {
    const bar = el("div", "outcome-bar", null, { role: "img", "aria-label": "Как заканчивались ответы" });
    const list = el("ul", "outcome-list");
    outcomes.forEach(([k, label, mark], i) => {
      const n = stats.statuses[k] || 0;
      if (n) {
        const seg_ = el("i", k);
        seg_.style.flexGrow = String(n);
        seg_.style.animationDelay = `${i * 90}ms`;
        bar.appendChild(seg_);
      }
      const li = el("li", k);
      li.append(el("i", null, mark), el("span", null, label), el("b", null, fmtNum(n)), el("small", null, `${Math.round((n / answered) * 100)} %`));
      list.appendChild(li);
    });
    outBody.append(bar, list);
  }
  const right = el("div", "stack");
  right.append(sheet("Популярные темы", `за ${days} ${plural(days, ["день", "дня", "дней"])}`, topicsBody), sheet("Чем заканчивались ответы", null, outBody));
  const leftCol = el("div", "stack");
  const row = el("div", "grid-2");
  row.append(leftCol, right);
  panel.appendChild(row);
  leftCol.appendChild(daysSheet);

  // ---- путь анкеты и сводка
  const steps = [
    [t.screenings_started, "Начали анкету", null],
    [t.documents_generated, "Получили документ", t.screenings_started],
    [t.tickets_sent, "Передали в деканат", t.documents_generated],
  ];
  const funnel = el("ol", "funnel");
  steps.forEach(([n, label, of], i) => {
    const li = el("li");
    li.style.animationDelay = `${i * 80}ms`;
    const m = el("div", "funnel-meter");
    const fill = el("i");
    fill.style.width = `${t.screenings_started ? Math.max(n ? 4 : 0, (n / t.screenings_started) * 100) : 0}%`;
    m.appendChild(fill);
    li.append(el("b", null, fmtNum(n)), el("span", null, label), el("small", null, of ? `${Math.round((n / of) * 100)} % от предыдущего шага` : `перевод — ${t.screenings_transfer}, зачисление — ${t.screenings_enrollment}`), m);
    funnel.appendChild(li);
  });
  const funnelSheet = sheet("Путь анкеты", `за ${days} ${plural(days, ["день", "дня", "дней"])}`, t.screenings_started ? funnel : empty("Анкет пока не было", "Когда студенты начнут проходить анкету, здесь появится путь до отправки."),
    t.tickets_declined ? el("p", "sheet-note", `Отказались передавать обращение: ${t.tickets_declined}. Документ они скачали и принесут сами.`) : document.createComment(""));

  const dl = el("dl", "ledger-list");
  const addRow = (k, v, cls) => {
    const r = el("div");
    r.append(el("dt", null, k), el("span", "dots"), el("dd", cls || null, v instanceof Node ? "" : String(v)));
    if (v instanceof Node) r.lastChild.appendChild(v);
    dl.appendChild(r);
  };
  const newLink = info.tickets_new ? button(String(info.tickets_new), () => { ticketFilter.status = "new"; selectTab("tickets"); }, "link-btn") : "0";
  addRow("Новых обращений", newLink);
  addRow("Шаблонов документов", fmtNum(info.templates));
  addRow("Документов в базе знаний", fmtNum(info.kb_documents));
  addRow("Пунктов Положения", fmtNum(info.regulation.clauses));
  addRow("Сейчас в системе", `${fmtNum(info.active_sessions)} ${plural(info.active_sessions, ["посетитель", "посетителя", "посетителей"])}`);
  addRow("Отправка обращений из анкеты", info.delivery.available ? "работает" : "сейчас недоступна", info.delivery.available ? "ok" : "warn");
  leftCol.append(funnelSheet, sheet("Сводка", null, dl));
}

// ---------------------------------------------------------------- обращения
async function viewTickets(panel) {
  const wrap = el("div", "grid-2");
  const left = sheet("Обращения по переводу и зачислению", null);
  const rightBox = el("div", "stack sticky");
  wrap.append(left, rightBox);
  panel.appendChild(wrap);

  const filtersBox = el("div", "filters-box");
  const filters = el("div", "filters");
  filtersBox.appendChild(filters);
  const mk = (label, node) => { filters.appendChild(field(label, node)); return node; };
  const from = mk("С даты", el("input", null, null, { type: "date" }));
  const to = mk("По дату", el("input", null, null, { type: "date" }));
  const type = mk("Тип", el("select"));
  const topic = mk("Тема", el("select"));
  from.value = ticketFilter.from;
  to.value = ticketFilter.to;
  const tally = el("div", "status-tally");
  const list = el("div", "items", null, { role: "listbox", "aria-label": "Обращения" });
  left.append(filtersBox, tally, list);

  let meta = null;
  let firstLoad = true;
  const fill = (sel, values, titles, current_) => {
    clear(sel);
    sel.appendChild(el("option", null, "все", { value: "" }));
    for (const v of values) sel.appendChild(el("option", null, titles ? titles[v] || v : v, { value: v }));
    sel.value = current_;
  };
  const query = (extra = {}) => new URLSearchParams({ date_from: ticketFilter.from, date_to: ticketFilter.to, type: ticketFilter.type, status: ticketFilter.status, topic: ticketFilter.topic, limit: "50", ...extra });
  const rows = () => [...list.querySelectorAll(".ticket")];
  const select = (it, id) => { openTicketId = id; for (const x of rows()) { x.classList.toggle("on", x === it); x.setAttribute("aria-selected", String(x === it)); } showTicket(id); };

  async function load() {
    const res = await guarded(() => api("GET", "/api/admin/tickets?" + query()));
    if (!res) return;
    if (!meta) {
      meta = res;
      fill(type, res.types, TYPE_TITLES, ticketFilter.type);
      fill(topic, res.topics, null, ticketFilter.topic);
    }
    const per = await Promise.all(meta.statuses.map((x) => api("GET", "/api/admin/tickets?" + query({ status: x, limit: "1" })).then((r) => [x, r.total])));
    const all = per.reduce((n, [, c]) => n + c, 0);
    clear(tally).appendChild(seg([["", `Все · ${all}`], ...per.map(([x, c]) => [x, `${STATUS_PLURAL[x]} · ${c}`])], ticketFilter.status, (v) => { ticketFilter.status = v; load(); }, "Статус"));
    // «Сбросить» появляется только когда что-то отфильтровано
    if (ticketFilter.from || ticketFilter.to || ticketFilter.type || ticketFilter.topic || ticketFilter.status) {
      tally.appendChild(button("Сбросить фильтры", () => {
        Object.assign(ticketFilter, { from: "", to: "", type: "", status: "", topic: "" });
        from.value = to.value = type.value = topic.value = "";
        load();
      }, "link-btn"));
    }
    clear(list);
    if (!res.items.length) list.appendChild(empty("Обращений нет", ticketFilter.status || ticketFilter.from || ticketFilter.to || ticketFilter.type || ticketFilter.topic ? "По этим условиям ничего не нашлось — попробуйте убрать часть фильтров." : "Когда студент передаст обращение из анкеты, оно появится здесь."));
    res.items.forEach((t, i) => {
      const closed = t.status === "done" || t.status === "rejected";
      const it = el("div", `item ticket${closed ? " closed" : ""}${t.id === openTicketId ? " on" : ""}`, null, { role: "option", tabindex: "0", "aria-selected": String(t.id === openTicketId) });
      it.style.animationDelay = `${Math.min(i * 35, 500)}ms`;
      // журнал входящих: номер — тема — когда — статус; тип в заголовок идёт, только если темы нет (иначе «Перевод · Перевод из другого вуза»)
      const main = el("div", "t-main");
      main.appendChild(el("h3", null, t.topic || TYPE_TITLES[t.type] || t.type));
      main.appendChild(el("p", "item-meta", [relTime(t.created_at), t.note ? "есть заметка" : ""].filter(Boolean).join(" · "), { title: fmtWhen(t.created_at) }));
      it.append(el("span", "t-no", `№ ${t.id}`), main, el("span", `status ${t.status}`, STATUS_TITLES[t.status] || t.status));
      it.addEventListener("click", () => select(it, t.id));
      it.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(it, t.id); return; }
        const step = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
        const all2 = rows();
        const next = step && all2[all2.indexOf(it) + step];
        if (next) { e.preventDefault(); next.focus(); next.click(); }
      });
      list.appendChild(it);
    });
    // зашли на вкладку — сразу показываем первое обращение (новое в приоритете): лишний клик не нужен
    if (firstLoad && !openTicketId && res.items.length) {
      firstLoad = false;
      const target = res.items.find((t) => t.status === "new") || res.items[0];
      select(rows()[res.items.indexOf(target)], target.id);
    }
    firstLoad = false;
  }

  async function showTicket(id) {
    const t = await guarded(() => api("GET", `/api/admin/tickets/${id}`));
    if (!t) return;
    clear(rightBox);
    const body = el("div");
    const fieldsEntries = Object.entries((t.payload || {}).fields || {});
    body.appendChild(el("p", "item-meta", `${TYPE_TITLES[t.type] || t.type} · ${t.topic || "без темы"} · ${fmtWhen(t.created_at)}`));
    const kv = el("dl", "kv");
    if ((t.payload || {}).request) kv.append(el("dt", null, "Что нужно"), el("dd", null, t.payload.request));
    for (const [k, v] of fieldsEntries) kv.append(el("dt", null, k), el("dd", null, v));
    body.appendChild(kv);
    if (!fieldsEntries.length) body.appendChild(el("p", "hint", "Студент не передал личные данные: в обращение попал только его документ."));
    if (t.has_attachment) {
      const a = el("a", "btn small", null, { href: `/api/admin/tickets/${t.id}/attachment` });
      a.append(icon("download"), document.createTextNode("Скачать заявление"));
      const wrap2 = el("div", "form-actions");
      wrap2.appendChild(a);
      body.appendChild(wrap2);
    }
    const headChip = el("span", `status ${t.status}`, STATUS_TITLES[t.status] || t.status);
    let status = t.status;
    const segBox = el("div");
    const drawSeg = () => clear(segBox).appendChild(seg(meta.statuses.map((x) => [x, STATUS_LABEL[x]]), status, async (v) => {
      if (v === status) return;
      const before = status;
      status = v;
      drawSeg();
      const ok = await guarded(async () => { await api("PATCH", `/api/admin/tickets/${t.id}`, { status: v }); return true; });
      if (!ok) { status = before; drawSeg(); return; }
      headChip.className = `status ${v}`;
      headChip.textContent = STATUS_TITLES[v];
      toast(`Статус: ${STATUS_TITLES[v]}`);
      load();
      refreshCounts();
    }, "Статус обращения"));
    drawSeg();
    const note = el("textarea", null, null, { id: "t-note", rows: "3", maxlength: "2000", placeholder: "Что сделано, кому передано — видно только сотрудникам" });
    note.value = t.note || "";
    let saved = note.value;
    const saveNote = button("Сохранить заметку", () => guarded(async () => {
      await api("PATCH", `/api/admin/tickets/${t.id}`, { note: note.value });
      saved = note.value;
      saveNote.classList.add("hidden");
      toast("Заметка сохранена");
      load();
    }), "small primary", "check");
    saveNote.classList.add("hidden");
    note.addEventListener("input", () => saveNote.classList.toggle("hidden", note.value === saved));
    note.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && note.value !== saved) saveNote.click(); });
    const noteActions = el("div", "form-actions");
    noteActions.append(saveNote, el("span", "hint", "Ctrl + Enter — сохранить"));
    noteActions.lastChild.classList.add("hidden");
    note.addEventListener("input", () => noteActions.lastChild.classList.toggle("hidden", note.value === saved));
    body.append(field("Статус", segBox), field("Заметка сотрудника", note, "t-note"), noteActions);
    rightBox.appendChild(sheet(`Обращение № ${t.id}`, headChip, body));
  }

  const onFilter = () => { ticketFilter.from = from.value; ticketFilter.to = to.value; ticketFilter.type = type.value; ticketFilter.topic = topic.value; load(); };
  for (const n of [from, to, type, topic]) n.addEventListener("change", onFilter);
  rightBox.appendChild(sheet("Обращение", null, empty("Выберите обращение", "Слева — список (можно переходить стрелками ↑ и ↓). Здесь появятся данные студента, заявление и статус.")));
  await load();
  if (openTicketId && !rows().length) openTicketId = null;
}

// ---------------------------------------------------------------- шаблоны
function placeholderChips(keys) {
  const chips = el("div", "chips");
  for (const k of keys) chips.appendChild(el("span", "chip", keyTitle(k)));
  return chips;
}

async function viewTemplates(panel) {
  const items = await api("GET", "/api/admin/templates");
  const grid = el("div", "grid-2");
  const left = el("div", "stack");
  const right = el("div", "stack sticky");
  grid.append(left, right);
  panel.appendChild(grid);

  // ---- список: главное действие — «Скачать», остальное в меню «⋯»
  const listBody = el("div", "items");
  if (!items.length) listBody.appendChild(empty("Шаблонов пока нет", "Пока их нет, студенты проходят анкету, но получить готовое заявление не смогут. Добавьте шаблон справа."));
  items.forEach((t, i) => {
    const it = el("div", "item");
    it.style.animationDelay = `${i * 50}ms`;
    const main = el("div");
    main.appendChild(el("h3", null, t.name));
    main.appendChild(el("p", "item-meta", `версия ${t.version} · обновлён ${fmtWhen(t.updated_at)}${t.created_by ? ` · ${ACTOR_TITLES[t.created_by] || t.created_by}` : ""}`));
    main.appendChild(t.placeholders.length ? placeholderChips(t.placeholders.map((p) => p.key)) : el("p", "hint", "В тексте нет данных для подстановки — документ получится пустым."));
    const actions = el("div", "actions");
    const dl = el("a", "btn small", null, { href: `/api/admin/templates/${encodeURIComponent(t.id)}/download` });
    dl.append(icon("download"), document.createTextNode("Скачать"));
    const replace = el("input", null, null, { type: "file", accept: ".docx", hidden: "", "aria-label": `Заменить файл шаблона «${t.name}»` });
    replace.addEventListener("change", () => guarded(async () => {
      const f = replace.files[0];
      if (!f) return;
      const form = new FormData();
      form.append("file", f);
      const r = await api("PUT", `/api/admin/templates/${encodeURIComponent(t.id)}`, form);
      toast(`Файл заменён — версия ${r.version}`);
      render();
      refreshCounts();
    }));
    actions.append(dl, replace, moreMenu(`Действия с шаблоном «${t.name}»`, [
      { text: "Заменить файл…", icon: "upload", run: () => replace.click() },
      { text: "История версий", icon: "history", run: () => guarded(() => showTemplateHistory(t)) },
      { text: "Удалить шаблон", icon: "trash", danger: true, run: () => guarded(async () => {
        if (!(await askConfirm({ title: `Удалить шаблон «${t.name}»?`, text: "Удалятся все его версии. Студенты, не закончившие анкету, получат сообщение, что шаблона больше нет." }))) return;
        await api("DELETE", `/api/admin/templates/${encodeURIComponent(t.id)}`);
        toast("Шаблон удалён");
        render();
        refreshCounts();
      }) },
    ]));
    it.append(main, actions);
    listBody.appendChild(it);
  });
  left.appendChild(sheet("Шаблоны документов", items.length ? `${items.length} ${plural(items.length, ["шаблон", "шаблона", "шаблонов"])}` : null, listBody));
  const historyBox = el("div");
  left.appendChild(historyBox);

  async function showTemplateHistory(t) {
    const h = await api("GET", `/api/admin/templates/${encodeURIComponent(t.id)}/history`);
    clear(historyBox);
    const tl = el("div");
    h.versions.forEach((v, i) => {
      const e = el("div", "event ok");
      e.style.animationDelay = `${i * 40}ms`;
      const text = el("div");
      const p = el("p");
      p.append(el("b", null, `Версия ${v.version}`), ` — ${v.note || "загружена"}${v.created_by ? `, ${ACTOR_TITLES[v.created_by] || v.created_by}` : ""}`);
      text.append(p, placeholderChips(v.placeholders));
      e.append(el("time", null, fmtWhen(v.created_at)), el("span", "pin"), text);
      tl.appendChild(e);
    });
    const c = sheet(`История шаблона «${t.name}»`, button("Закрыть", () => clear(historyBox), "small ghost"), tl);
    historyBox.appendChild(c);
    c.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "nearest" });
  }

  // ---- добавить: кнопка оживает, когда выбран файл
  const nameIn = el("input", null, null, { type: "text", id: "t-name", maxlength: "120", placeholder: "Например, «Заявление о переводе»" });
  const target = el("select", null, null, { id: "t-target" });
  target.appendChild(el("option", null, "Добавить как новый шаблон", { value: "" }));
  for (const t of items) target.appendChild(el("option", null, `Заменить «${t.name}»`, { value: t.id }));
  let upload = null;
  const pick = filePick("t-file", ".docx", "Файл Word (.docx) с оформлением, шапкой и подписями", (f) => { if (upload) upload.disabled = !f; });
  upload = button("Сохранить шаблон", () => guarded(async () => {
    const f = pick.input.files[0];
    if (!f) return;
    const form = new FormData();
    form.append("file", f);
    form.append("name", nameIn.value);
    form.append("template_id", target.value);
    form.append("replace", target.value ? "true" : "false");
    const info = await api("POST", "/api/admin/templates", form);
    const keys = info.placeholders.map((p) => keyTitle(p.key)).join(", ");
    await render(); // перерисовка очищает баннер, поэтому сообщение — после неё
    showBanner($("banner"), info.placeholders.length ? "ok" : "warn", info.placeholders.length
      ? `Шаблон «${info.name}» сохранён (версия ${info.version}). Подставляются: ${keys}.`
      : `Шаблон «${info.name}» сохранён, но в тексте нет мест для данных студента. Нажмите на название данных справа, чтобы скопировать метку, и вставьте её в документ.`);
    refreshCounts();
  }), "primary", "upload");
  upload.disabled = true;
  const actions = el("div", "form-actions block");
  actions.append(upload, el("span", "hint", "Сначала выберите файл"));
  pick.input.addEventListener("change", () => actions.lastChild.classList.toggle("hidden", !!pick.input.files[0]));
  right.appendChild(sheet("Добавить шаблон", null, field("Что сделать", target, "t-target"), field("Название", nameIn, "t-name", "(необязательно)"), pick.node, actions));

  // ---- какие данные подставляются
  const chips = el("div", "chips");
  for (const [key, title] of Object.entries(FIELD_TITLES)) {
    const b = el("button", "chip", title, { type: "button", title: "Скопировать метку для вставки в шаблон" });
    b.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(`{{ ${key} }}`); toast(`Метка «${title}» скопирована — вставьте её в документ Word`); } catch (e) { toast("Не удалось скопировать — разрешите доступ к буферу обмена"); }
    });
    chips.appendChild(b);
  }
  right.appendChild(sheet("Какие данные подставляются", null, chips, el("p", "sheet-note", "Нажмите на название — метка скопируется. Вставьте её в документ Word там, где должно появиться значение. Свои метки (например, «{{ номер_зачётки }}») тоже допустимы. Если данных от студента нет, в готовом заявлении останется строка «________» для заполнения от руки.")));
}

// ---------------------------------------------------------------- база знаний
/** Основное Положение называется по-человечески, а не именем файла (polozhenie.md). */
const kbTitle = (d) => (d.kind === "base" ? "Основное Положение" : d.name);

async function viewKb(panel) {
  const items = await api("GET", "/api/admin/kb");
  const grid = el("div", "grid-2");
  const left = el("div", "stack");
  const right = el("div", "stack sticky");
  grid.append(left, right);
  panel.appendChild(grid);

  const listBody = el("div", "items");
  items.forEach((d, i) => {
    const it = el("div", `item${d.active ? "" : " off"}`);
    it.style.animationDelay = `${i * 50}ms`;
    const main = el("div");
    main.appendChild(el("h3", null, kbTitle(d)));
    main.appendChild(el("p", "item-meta", d.updated_at ? `обновлён ${fmtWhen(d.updated_at)}${d.created_by ? ` · ${ACTOR_TITLES[d.created_by] || d.created_by}` : ""}` : "исходный файл системы — ещё не заменялся"));
    const chips = el("div", "chips");
    chips.append(el("span", "chip", d.kind === "base" ? "действует для всех ответов" : "дополнительный документ"), el("span", "chip", `${d.clauses} ${plural(d.clauses, ["пункт", "пункта", "пунктов"])}`), el("span", "chip", `версия ${d.version || 1}`));
    main.appendChild(chips);
    const actions = el("div", "actions");
    const replace = el("input", null, null, { type: "file", accept: ".txt,.md,.docx,.pdf", hidden: "", "aria-label": `Заменить файл «${kbTitle(d)}»` });
    replace.addEventListener("change", () => guarded(async () => {
      const f = replace.files[0];
      if (!f) return;
      const form = new FormData();
      form.append("file", f);
      const r = await api("PUT", `/api/admin/kb/${encodeURIComponent(d.id)}`, form);
      toast(`Документ заменён: ${r.clauses} ${plural(r.clauses, ["пункт", "пункта", "пунктов"])}, версия ${r.version}`);
      await render();
      if (r.notice) showBanner($("banner"), "warn", r.notice);
    }));
    const isBase = d.kind === "base";
    actions.append(
      replace,
      toggle(d.active, "В ответах", async (on) => {
        const ok = await guarded(async () => { await api("PATCH", `/api/admin/kb/${encodeURIComponent(d.id)}`, { active: on }); return true; });
        if (!ok) return render(); // не получилось — возвращаем переключатель в прежнее положение
        it.classList.toggle("off", !on);
        toast(on ? `«${kbTitle(d)}» снова используется в ответах` : `«${kbTitle(d)}» отключён — секретарь по нему не отвечает`);
      }),
      moreMenu(`Действия с документом «${kbTitle(d)}»`, [
        { text: "Заменить файл…", icon: "upload", run: () => replace.click() },
        { text: "История версий", icon: "history", run: () => guarded(() => showKbHistory(d)) },
        isBase
          ? { text: "Вернуть Положение из файла", icon: "history", danger: true, run: () => guarded(async () => {
              if (!(await askConfirm({ title: "Вернуть основное Положение из файла?", text: "Загруженная замена перестанет действовать, но останется в истории.", ok: "Вернуть" }))) return;
              await api("DELETE", `/api/admin/kb/${encodeURIComponent(d.id)}`);
              toast("Положение возвращено из файла");
              render();
            }) }
          : { text: "Удалить документ", icon: "trash", danger: true, run: () => guarded(async () => {
              if (!(await askConfirm({ title: `Удалить «${kbTitle(d)}»?`, text: "Его пункты перестанут использоваться в ответах секретаря." }))) return;
              await api("DELETE", `/api/admin/kb/${encodeURIComponent(d.id)}`);
              toast("Документ удалён");
              render();
              refreshCounts();
            }) },
      ]),
    );
    it.append(main, actions);
    listBody.appendChild(it);
  });
  const reindex = button("Обновить поиск", () => guarded(async () => {
    const r = await api("POST", "/api/admin/reindex");
    toast(`Поиск обновлён: ${r.clauses} ${plural(r.clauses, ["пункт", "пункта", "пунктов"])} из ${r.documents} ${plural(r.documents, ["документа", "документов", "документов"])}`.replace("1 документов", "1 документа"));
  }), "small ghost", "spark");
  reindex.title = "Пересобрать поиск по всем включённым документам";
  left.appendChild(sheet("Документы, по которым отвечает секретарь", reindex, listBody));
  const historyBox = el("div");
  left.appendChild(historyBox);

  async function showKbHistory(d) {
    const h = await api("GET", `/api/admin/kb/${encodeURIComponent(d.id)}/history`);
    clear(historyBox);
    const tl = el("div");
    h.versions.forEach((v, i) => {
      const e = el("div", "event ok");
      e.style.animationDelay = `${i * 40}ms`;
      const p = el("p");
      p.append(el("b", null, `Версия ${v.version}`), ` — ${v.clauses} ${plural(v.clauses, ["пункт", "пункта", "пунктов"])}${v.created_by ? `, ${ACTOR_TITLES[v.created_by] || v.created_by}` : ""}`);
      e.append(el("time", null, fmtWhen(v.created_at)), el("span", "pin"), p);
      tl.appendChild(e);
    });
    const c = sheet(`История: «${kbTitle(d)}»`, button("Закрыть", () => clear(historyBox), "small ghost"), h.versions.length ? tl : empty("Изменений не было", "Документ ещё не заменяли."));
    historyBox.appendChild(c);
    c.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "nearest" });
  }

  const nameIn = el("input", null, null, { type: "text", id: "k-name", maxlength: "120", placeholder: "Например, «Положение о переводе»" });
  let addBtn = null;
  const pick = filePick("k-file", ".txt,.md,.docx,.pdf", "Текст, Word или PDF с текстовым слоем", (f) => { if (addBtn) addBtn.disabled = !f; });
  addBtn = button("Добавить документ", () => guarded(async () => {
    const f = pick.input.files[0];
    if (!f) return;
    const form = new FormData();
    form.append("file", f);
    form.append("name", nameIn.value);
    const r = await api("POST", "/api/admin/kb", form);
    const done = `Документ «${r.name}» добавлен: ${r.clauses} ${plural(r.clauses, ["пункт", "пункта", "пунктов"])}. Секретарь уже отвечает и по нему.`;
    await render();
    showBanner($("banner"), r.notice ? "warn" : "ok", r.notice ? `${done} ${r.notice}` : done);
    refreshCounts();
  }), "primary", "upload");
  addBtn.disabled = true;
  const actions = el("div", "form-actions block");
  actions.append(addBtn, el("span", "hint", "Сначала выберите файл"));
  pick.input.addEventListener("change", () => actions.lastChild.classList.toggle("hidden", !!pick.input.files[0]));
  right.appendChild(sheet("Добавить документ", null, field("Название", nameIn, "k-name", "(необязательно)"), pick.node, actions,
    el("p", "sheet-note", "Нумерация пунктов («3.4.», «п. 3.4») определяется сама, поиск обновляется сразу. Прежние версии сохраняются в истории.")));
}

// ---------------------------------------------------------------- история
const dayLabel = (d) => {
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(new Date()) - start(d)) / 86400000);
  if (diff === 0) return "Сегодня";
  if (diff === 1) return "Вчера";
  return `${WEEKDAYS[d.getDay()][0].toUpperCase()}${WEEKDAYS[d.getDay()].slice(1)}, ${d.getDate()} ${MONTHS[d.getMonth()]}${d.getFullYear() === new Date().getFullYear() ? "" : ` ${d.getFullYear()}`}`;
};
let historyKind = "";

/** Что изменилось: «№ 2» для обращения, название для шаблона и документа, без служебных идентификаторов. */
function objectLabel(l) {
  if (l.object_type === "admin" || l.object_type === "storage" || !l.object_id) return "";
  if (l.object_type === "ticket") return ` № ${l.object_id}`;
  if (l.object_id === "base") return " «Основное Положение»";
  const name = l.details && l.details.name;
  return ` «${name || l.object_id}»`;
}

async function viewHistory(panel) {
  const log = await api("GET", "/api/admin/audit?limit=200");
  const kinds = [["", "Всё"], ["template", "Шаблоны"], ["kb", "База знаний"], ["ticket", "Обращения"], ["admin", "Входы"]];
  const body = el("div");
  const draw = () => {
    clear(body);
    const rows = log.filter((l) => !historyKind || l.object_type === historyKind);
    if (!rows.length) return body.appendChild(empty("Записей нет", "Здесь появятся входы, загрузки шаблонов и документов, изменения обращений."));
    let day = "";
    rows.forEach((l, i) => {
      const d = new Date(l.ts);
      const label = dayLabel(d);
      if (label !== day) {
        day = label;
        body.appendChild(el("h3", "timeline-day", label));
      }
      const bad = l.action === "admin.login_failed" || /delete/.test(l.action);
      const good = /create|replace/.test(l.action);
      const e = el("div", `event${bad ? " bad" : good ? " ok" : ""}`);
      e.style.animationDelay = `${Math.min(i * 20, 400)}ms`;
      const text = el("div");
      const p = el("p");
      p.append(el("b", null, ACTOR_TITLES[l.actor] || l.actor), ` — ${ACTION_TITLES[l.action] || l.action}${objectLabel(l)}`);
      text.appendChild(p);
      const details = humanDetails(l.details);
      if (details) text.appendChild(el("small", null, details));
      e.append(el("time", null, d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })), el("span", "pin"), text);
      body.appendChild(e);
    });
  };
  const filter = el("div");
  const drawFilter = () => clear(filter).appendChild(seg(kinds, historyKind, (v) => { historyKind = v; drawFilter(); draw(); }, "Что показывать"));
  drawFilter();
  draw();
  const purge = button("Удалить просроченные данные сейчас", () => guarded(async () => {
    if (!(await askConfirm({ title: "Удалить просроченные данные?", text: "Будут удалены анкеты, готовые файлы и обращения, у которых истёк срок хранения. Остальное не тронется.", ok: "Удалить" }))) return;
    const r = await api("POST", "/api/admin/purge");
    const text = humanDetails(r);
    toast(text ? `Удалено по сроку хранения — ${text}` : "Просроченных данных нет");
  }), "link-btn");
  const note = el("p", "sheet-note");
  note.append("Пароли и содержимое документов в журнал не попадают. Старые данные удаляются по сроку хранения сами; ", purge, ".");
  panel.appendChild(sheet("История изменений", filter, body, note));
}

// ---------------------------------------------------------------- аккаунт
async function viewAccount(panel) {
  const cur = el("input", null, null, { type: "password", id: "pw-cur", autocomplete: "current-password" });
  const nw = el("input", null, null, { type: "password", id: "pw-new", autocomplete: "new-password" });
  const btn = button("Сменить пароль", () => guarded(async () => {
    await api("POST", "/api/admin/password", { current: cur.value, new: nw.value });
    cur.value = "";
    nw.value = "";
    showBanner($("banner"), "ok", "Пароль изменён. Остальные ваши входы завершены.");
  }), "primary", "key");
  const actions = el("div", "form-actions");
  actions.appendChild(btn);
  const rules = el("ul", "rules");
  for (const text of ["Не короче 10 символов и не из простых — «пароль123» не подойдёт.", "После смены пароля вы останетесь здесь, а входы с других устройств завершатся.",
    "После нескольких неверных попыток вход на время закрывается — это защита от подбора.", "Вход, смена пароля и все правки попадают в историю."]) rules.appendChild(el("li", null, text));
  const grid = el("div", "grid-2 even");
  grid.append(sheet("Смена пароля", null, field("Текущий пароль", cur, "pw-cur"), field("Новый пароль", nw, "pw-new"), actions), sheet("Как защищён кабинет", null, rules));
  panel.appendChild(grid);
}

// ---------------------------------------------------------------- вход / выход
async function start() {
  try {
    const me = await api("GET", "/api/admin/me");
    const who = clear($("who"));
    who.append(el("i", null, (me.username[0] || "?").toUpperCase()), el("span", null, me.username));
    who.classList.remove("hidden");
    $("login").classList.add("hidden");
    $("app").classList.remove("hidden");
    $("btn-logout").classList.remove("hidden");
    $("today").textContent = new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
    renderTabs();
    refreshCounts().catch(() => null);
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
const reveal = el("button", "link-btn reveal", "Показать", { type: "button", "aria-controls": "p" });
reveal.addEventListener("click", () => {
  const hidden = $("p").type === "password";
  $("p").type = hidden ? "text" : "password";
  reveal.textContent = hidden ? "Скрыть" : "Показать";
});
document.querySelector('label[for="p"]').appendChild(reveal);
$("btn-logout").prepend(icon("logout"));
$("btn-logout").addEventListener("click", async () => {
  await api("POST", "/api/admin/logout").catch(() => null);
  showLogin("Вы вышли.");
});

start();
