/* Кабинет сотрудника: вкладка «Обзор»: статистика, графики, воронка. Подключается после admin.js (общие помощники и каркас) и common.js. */
"use strict";

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
  const daysSheet = sheet("Вопросы и проверки по дням", total ? asTable : null, total ? holder : empty("Пока тихо", "За выбранный период вопросов и проверок документов не было."), total ? foot : document.createComment(""));

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
  const tg = info.telegram || { enabled: false };
  if (tg.enabled) {
    // бот включён: работает ли он и сколько людей им пользуется (аккаунты необязательны, поэтому число небольшое — это нормально)
    const state = tg.running && !tg.error ? "работает" + (tg.bot ? ` (@${tg.bot})` : "") : (tg.error || "запускается");
    addRow("Telegram-бот", state, tg.running && !tg.error ? "ok" : "warn");
    addRow("Аккаунтов Telegram", `${fmtNum(tg.users)}, из них входили на сайте — ${fmtNum(tg.web_linked)}`);
  } else {
    addRow("Telegram-бот", "выключен");
  }
  leftCol.append(funnelSheet, sheet("Сводка", null, dl));
}
