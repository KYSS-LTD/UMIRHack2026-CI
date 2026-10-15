/* Кабинет сотрудника: вкладка «Шаблоны»: DOCX-шаблоны и их версии. Подключается после admin.js (общие помощники и каркас) и common.js. */
"use strict";

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
