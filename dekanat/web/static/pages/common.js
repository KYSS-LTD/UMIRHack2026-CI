/* Общие помощники страниц. Весь пользовательский текст выводится только через textContent — HTML из данных не вставляется.
   Стили задаются классами или через CSSOM (element.style), что разрешено строгим CSP. */
"use strict";

const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function el(tag, cls, text, attrs) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  if (attrs) for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

// ---------------------------------------------------------------- иконки (тонкий контур, как в основном интерфейсе)
const ICONS = {
  doc: "M7 3h7l5 5v13H7zM14 3v5h5M10 13h5M10 17h3",
  upload: "M12 16V4M7 9l5-5 5 5M5 20h14",
  download: "M12 4v12M7 11l5 5 5-5M5 20h14",
  check: "M5 12.5l4.5 4.5L19 7",
  alert: "M12 8v5M12 16.5v.5M10.3 3.9L2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  shield: "M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6zM9 12l2 2 4-4",
  book: "M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 5v16",
  spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
  x: "M6 6l12 12M18 6L6 18",
  chart: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  inbox: "M3 13l3-8h12l3 8v6H3zM3 13h5l1 3h6l1-3h5",
  history: "M3 12a9 9 0 1 0 2.6-6.4M3 4v4h4M12 7v5l3 2",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c0-4 4-6 8-6s8 2 8 6",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
  logout: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11",
  key: "M15 7a4 4 0 1 1-3.9 5H3v3h3v2h3v-2h2.1A4 4 0 0 1 15 7zM16 10.5h.01",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  filter: "M4 5h16l-6.5 7.5V19l-3 1.5v-8z",
  up: "M6 15l6-6 6 6",
  down: "M6 9l6 6 6-6",
};

function icon(name, cls) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", ICONS[name] || ICONS.spark);
  svg.appendChild(path);
  return svg;
}

// ---------------------------------------------------------------- анимации
/** Плавный отсчёт числа для плиток статистики. */
function countUp(node, value, ms = 900) {
  const target = Number(value) || 0;
  if (REDUCED || target === 0) {
    node.textContent = String(target);
    return;
  }
  const start = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - start) / ms);
    node.textContent = String(Math.round(target * (1 - Math.pow(1 - k, 3))));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/** Подсветка границы «острова» вслед за курсором. */
function islandGlow() {
  for (const island of document.querySelectorAll(".island")) {
    island.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "mouse") return;
      const r = island.getBoundingClientRect();
      island.style.setProperty("--mx", `${e.clientX - r.left}px`);
      island.style.setProperty("--my", `${e.clientY - r.top}px`);
    });
  }
}

// ---------------------------------------------------------------- сеть
class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// стандартные английские тексты FastAPI («Not Found» для неизвестного адреса) — не для людей: такое бывает, когда сервер запущен со старой версией проекта
const STALE_SERVER = "Сервер не знает этого действия: скорее всего, он запущен со старой версией проекта. Остановите сервер, запустите его заново и обновите страницу.";
const DEFAULT_DETAILS = { "Not Found": STALE_SERVER, "Method Not Allowed": STALE_SERVER };

async function api(method, path, body) {
  const init = { method, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new ApiError(0, "Нет связи с сервером. Проверьте, что он запущен, и повторите.");
  }
  let data = null;
  const type = res.headers.get("content-type") || "";
  if (type.includes("application/json")) data = await res.json().catch(() => null);
  if (!res.ok) {
    let msg = data && data.detail ? data.detail : `Ошибка ${res.status}`;
    if (Array.isArray(msg)) msg = msg.map((m) => m.msg || String(m)).join("; ");
    if (DEFAULT_DETAILS[msg]) msg = DEFAULT_DETAILS[msg];
    throw new ApiError(res.status, String(msg));
  }
  return data;
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

// ---------------------------------------------------------------- даты и числа по-русски
const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const WEEKDAYS = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];

function plural(n, forms) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  return forms[a > 10 && a < 20 ? 2 : b === 1 ? 0 : b >= 2 && b <= 4 ? 1 : 2];
}

function fmtNum(n) {
  return Number(n || 0).toLocaleString("ru-RU");
}

/** «2026-10-14» → «14 окт» (long: «вторник, 14 октября»). День статистики — дата в UTC. */
function fmtDay(day, long) {
  const d = new Date(`${day}T00:00:00Z`);
  if (isNaN(d)) return day;
  return long ? `${WEEKDAYS[d.getUTCDay()]}, ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}` : `${d.getUTCDate()} ${MONTHS_SHORT[d.getUTCMonth()]}`;
}

/** «сегодня, 10:32» · «вчера, 18:05» · «12 окт, 09:10» · «12 окт 2025, 09:10». */
function fmtWhen(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  const time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const today = new Date();
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((start(today) - start(d)) / 86400000);
  if (days === 0) return `сегодня, ${time}`;
  if (days === 1) return `вчера, ${time}`;
  const year = d.getFullYear() === today.getFullYear() ? "" : ` ${d.getFullYear()}`;
  return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}${year}, ${time}`;
}

/** «только что» · «5 мин назад» · «2 ч назад» · дальше — дата. */
function relTime(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return fmtWhen(iso);
  const min = Math.round((Date.now() - d.getTime()) / 60000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин назад`;
  if (min < 24 * 60) return `${Math.round(min / 60)} ч назад`;
  return fmtWhen(iso);
}

function showBanner(container, kind, text) {
  clear(container);
  if (!text) return;
  const b = el("p", `banner ${kind}`, null, { role: kind === "bad" ? "alert" : "status" });
  b.appendChild(icon(kind === "ok" ? "check" : "alert"));
  b.appendChild(el("span", null, text));
  container.appendChild(b);
}

// Тема общая с «Столом декана»: тот же ключ localStorage, значения system | light | dark.
(function initTheme() {
  const KEY = "dekanat.theme";
  const ORDER = ["system", "light", "dark"];
  const GLYPH = { system: "◐", light: "☀", dark: "☾" };
  let theme = "system";
  try {
    const v = localStorage.getItem(KEY);
    if (ORDER.includes(v)) theme = v;
  } catch (e) { /* хранилище недоступно — остаётся «как в системе» */ }
  const apply = () => {
    document.documentElement.dataset.theme = theme;
    const btn = document.getElementById("theme-btn");
    if (btn) {
      btn.textContent = GLYPH[theme];
      btn.title = "Тема: " + { system: "как в системе", light: "светлая", dark: "тёмная" }[theme];
    }
    try { localStorage.setItem(KEY, theme); } catch (e) { /* не критично */ }
  };
  document.documentElement.dataset.theme = theme;
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    islandGlow();
    const btn = document.getElementById("theme-btn");
    if (btn) btn.addEventListener("click", () => { theme = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length]; apply(); });
  });
})();
