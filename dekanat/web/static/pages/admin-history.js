/* Кабинет сотрудника: вкладка «История»: журнал изменений. Подключается после admin.js (общие помощники и каркас) и common.js. */
"use strict";

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
