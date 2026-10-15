/* Кабинет сотрудника (/admin). Весь текст из данных выводится через textContent; стили — классами или через CSSOM (строгий CSP). */
"use strict";

const $ = (id) => document.getElementById(id);

const TABS = [
  ["overview", "Обзор"],
  ["tickets", "Обращения"],
  ["templates", "Шаблоны"],
  ["kb", "База знаний"],
  ["types", "Типы документов"],
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
  "admin.password_change": "сменил(а) пароль", "admin.password_reset": "пароль сброшен ответственным за сервер", "template.create": "добавлен шаблон", "template.replace": "заменён шаблон", "template.rename": "переименован шаблон",
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
  requestAnimationFrame(() => $("u").focus({ preventScroll: true }));
}

/** Поле пароля с кнопкой «показать / скрыть» внутри. Сам input остаётся тем же элементом: id, autocomplete и подписи не меняются. */
function passwordBox(input) {
  const box = el("div", "pw-box");
  const eye = el("button", "pw-eye", null, { type: "button", "aria-label": "Показать пароль", "aria-pressed": "false", title: "Показать пароль" });
  eye.appendChild(icon("eye"));
  eye.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    eye.setAttribute("aria-pressed", String(show));
    eye.setAttribute("aria-label", show ? "Скрыть пароль" : "Показать пароль");
    eye.title = eye.getAttribute("aria-label");
    eye.replaceChildren(icon(show ? "eyeOff" : "eye"));
    input.focus();
  });
  input.replaceWith(box);
  box.append(input, eye);
  return box;
}

let toastTimer = 0;
/** Короткое сообщение внизу. undo — действие «Отменить»: кнопка живёт в сообщении несколько секунд дольше обычного. */
function toast(text, undo) {
  let t = document.querySelector(".toast");
  if (!t) {
    t = el("div", "toast", null, { role: "status" });
    document.body.appendChild(t);
  }
  t.textContent = text;
  t.classList.toggle("has-action", !!undo);
  if (undo) {
    const b = el("button", "toast-undo", "Отменить", { type: "button" });
    b.addEventListener("click", () => { t.classList.remove("on"); clearTimeout(toastTimer); undo(); });
    t.appendChild(b);
  }
  t.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("on"), undo ? 7000 : 2200);
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

const segMemory = {}; // подпись переключателя -> где стоял ползунок: после перерисовки панели он едет от прежнего места, а не появляется на новом

/** Переключатель с ползунком: выбранный вариант «подъезжает» плавно, а не вспыхивает. */
function seg(options, value, onPick, label, statusOf) {
  const box = el("div", "seg", null, { role: "group", "aria-label": label });
  const thumb = el("i", "seg-thumb", null, { "aria-hidden": "true" });
  box.appendChild(thumb);
  const place = (animate) => {
    const on = box.querySelector('button[aria-pressed="true"]');
    if (!on || !box.offsetWidth) return;
    const at = { x: on.offsetLeft, y: on.offsetTop, w: on.offsetWidth, h: on.offsetHeight };
    const put = (p) => { for (const [k, v] of Object.entries(p)) thumb.style.setProperty(`--${k}`, `${v}px`); };
    const from = segMemory[label];
    if (animate && from && !REDUCED) {
      thumb.classList.add("no-motion");
      put(from);
      void thumb.offsetWidth; // зафиксировать прежнее место, затем поехать
      thumb.classList.remove("no-motion");
    } else thumb.classList.add("no-motion");
    put(at);
    if (!animate || !from) requestAnimationFrame(() => thumb.classList.remove("no-motion"));
    if (on.dataset.st) thumb.dataset.st = on.dataset.st;
    segMemory[label] = at;
  };
  for (const [v, text] of options) {
    const b = el("button", null, text, { type: "button", "aria-pressed": String(v === value) });
    if (statusOf) b.dataset.st = statusOf(v);
    b.addEventListener("click", () => {
      if (v === value) return;
      for (const x of box.querySelectorAll("button")) x.setAttribute("aria-pressed", String(x === b));
      value = v;
      place(true); // ползунок уже едет; перерисовка панели — когда он почти доехал
      setTimeout(() => onPick(v), REDUCED ? 0 : 260);
    });
    box.appendChild(b);
  }
  setTimeout(() => place(true), 0); // когда переключатель уже в странице; запасной ход на случай, если кадры не рисуются (вкладка в фоне)
  requestAnimationFrame(() => place(true));
  if (window.ResizeObserver) new ResizeObserver(() => place(false)).observe(box);
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
    if (it.disabled) b.disabled = true;
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

/** Флажок в стиле кабинета: настоящий input (клавиатура, скринридер), нарисован рамкой и галочкой; ни метка, ни флажок не «проваливаются» в строку. */
function tick(input) {
  const label = el("label", "tick");
  label.append(input, el("span", "tick-box", null, { "aria-hidden": "true" }));
  label.addEventListener("click", (e) => e.stopPropagation());
  return label;
}

const foldText = (x) => String(x || "").toLowerCase().replace(/ё/g, "е");

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

/** Если распознавание текста не работает, сотрудник видит причину и точные шаги: студенты получают только короткую плашку. */
function renderOcrAlert(info) {
  const box = $("ocr-alert");
  const ocr = info.ocr;
  clear(box);
  box.classList.toggle("hidden", !ocr || ocr.available !== false);
  if (!ocr || ocr.available !== false) return;
  box.appendChild(el("strong", null, "Проверка документов сейчас не работает"));
  box.appendChild(el("p", null, ocr.reason ? `Причина: ${ocr.reason}.` : "Распознавание текста не запускается."));
  if (ocr.fix) box.appendChild(el("p", null, ocr.fix));
  const cmd = ocr.cmd ? el("p", "ocr-where", `Найден Tesseract: ${ocr.cmd}${ocr.tessdata ? ` · языковые данные: ${ocr.tessdata}` : ""}`) : null;
  if (cmd) box.appendChild(cmd);
  box.appendChild(el("p", "hint", "После исправления перезапустите сервер и обновите страницу. Вопросы по Положению и анкета работают и сейчас."));
}

function renderSummary(info) {
  renderOcrAlert(info);
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
  const views = { overview: viewOverview, tickets: viewTickets, templates: viewTemplates, kb: viewKb, types: viewTypes, history: viewHistory, account: viewAccount };
  await guarded(() => views[current](wrap));
  if (token === renderToken) {
    host.style.minHeight = "0px"; // высота отпускается плавно (transition у #panel)
    setTimeout(() => { if (token === renderToken) host.style.minHeight = ""; }, 360);
  }
}
