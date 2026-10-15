/* Кабинет сотрудника: вкладка «База знаний»: документы Положения, проверка ответов. Подключается после admin.js (общие помощники и каркас) и common.js. */
"use strict";

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
  }), "small", "refresh");
  reindex.title = "Пересобрать поиск по всем включённым документам";
  const check = button("Проверить ответы", () => guarded(async () => {
    const r = await api("POST", "/api/admin/kb/check");
    clear(historyBox);
    const rows = r.items.map((x) => [x.question, x.expected, x.got, el("span", `check-mark ${x.ok ? "ok" : "bad"}`, x.ok ? "верно" : "не так")]);
    const verdict = el("p", r.passed === r.total ? "check-sum ok" : "check-sum bad", r.passed === r.total
      ? `Все ${r.total} контрольных вопросов отвечены как ожидалось.`
      : `Отвечено как ожидалось: ${r.passed} из ${r.total}. Проверьте строки с пометкой «не так».`);
    const c = sheet("Проверка ответов по контрольным вопросам", button("Закрыть", () => clear(historyBox), "small ghost"), verdict, table(["Вопрос", "Ожидалось", "Получено", ""], rows),
      el("p", "sheet-note", "Вопросы и ожидаемые пункты — в config/golden_questions.yaml; они написаны для учебного Положения. Заменили Положение — замените и вопросы."));
    historyBox.appendChild(c);
    c.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "nearest" });
  }), "small", "check");
  check.title = "Задать секретарю контрольные вопросы и сверить ответы с ожидаемыми пунктами";
  const kbTools = el("div", "actions");
  kbTools.append(check, reindex);
  const historyBox = el("div");
  left.appendChild(sheet("Документы, по которым отвечает секретарь", kbTools, listBody));
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
