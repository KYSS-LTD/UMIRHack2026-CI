/* Кабинет сотрудника: вкладка «Типы документов»: загрузка, правка и удаление описаний. Подключается после admin.js (общие помощники и каркас) и common.js. */
"use strict";

// ---------------------------------------------------------------- типы документов
async function viewTypes(panel) {
  const items = await api("GET", "/api/admin/doctypes");
  const grid = el("div", "grid-2");
  const left = el("div", "stack");
  const right = el("div", "stack sticky");
  grid.append(left, right);
  panel.appendChild(grid);

  let editing = null; // id изменяемого типа; null — добавляем новый
  const area = el("textarea", "code-area", null, { id: "t-yaml", rows: "16", spellcheck: "false", "aria-label": "Описание типа документа (YAML)", placeholder: "Выберите файл .yaml или вставьте описание сюда" });
  const verdict = el("p", "check-sum hidden", null, { role: "status" });
  const heading = el("span", "aside", "новый тип");
  let saveBtn = null;
  let checked = null; // последний разбор описания: {ok, id, ...}

  const HINT_EMPTY = "Выберите файл или вставьте описание выше";
  const sync = (r) => {
    const ready = !!r && r.ok && (editing || !r.exists);
    hint.classList.toggle("hidden", ready);
    hint.textContent = !r ? HINT_EMPTY : !r.ok ? "Исправьте описание — кнопка станет активной" : "Измените id в описании, чтобы добавить новый тип";
  };
  const showVerdict = (r) => {
    checked = r;
    verdict.classList.remove("hidden", "ok", "bad");
    if (!r.ok) {
      verdict.classList.add("bad");
      verdict.textContent = r.error;
    } else {
      verdict.classList.add(!editing && r.exists ? "bad" : "ok");
      verdict.textContent = !editing && r.exists
        ? `Тип с id «${r.id}» уже есть. Измените id в описании или откройте этот тип на изменение.`
        : `Описание верное: «${r.title}», id «${r.id}», ${r.fields} ${plural(r.fields, ["поле", "поля", "полей"])} (${r.field_titles.slice(0, 6).join(", ")}${r.field_titles.length > 6 ? "…" : ""}).`;
    }
    saveBtn.disabled = !(r.ok && (editing || !r.exists));
    sync(r);
  };
  const check = async () => {
    if (!area.value.trim()) { verdict.classList.add("hidden"); saveBtn.disabled = true; sync(null); return; }
    showVerdict(await api("POST", "/api/admin/doctypes/validate", { yaml: area.value }));
  };
  const setText = (text) => { area.value = text; return guarded(check); };
  let typing = 0;
  area.addEventListener("input", () => { saveBtn.disabled = true; clearTimeout(typing); typing = setTimeout(() => guarded(check), 500); });

  const pick = filePick("t-file", ".yaml,.yml,.txt", "Файл описания типа, .yaml", async (f) => {
    if (f) { editing = null; heading.textContent = "новый тип"; await setText(await f.text()); }
  });
  const reset = () => { editing = null; heading.textContent = "новый тип"; area.value = ""; pick.reset(); verdict.classList.add("hidden"); saveBtn.disabled = true; saveBtn.lastChild.textContent = "Добавить тип"; sync(null); };
  saveBtn = button("Добавить тип", () => guarded(async () => {
    const r = editing
      ? await api("PUT", `/api/admin/doctypes/${encodeURIComponent(editing)}`, { yaml: area.value })
      : await api("POST", "/api/admin/doctypes", { yaml: area.value });
    await render();
    showBanner($("banner"), "ok", r.created ? `Тип «${r.title}» добавлен: секретарь уже узнаёт такие документы, и он есть в списке типов на столе.` : `Изменения в типе «${r.title}» сохранены.`);
  }), "primary", "check");
  saveBtn.disabled = true;
  const edit = (d) => guarded(async () => {
    const r = await api("GET", `/api/admin/doctypes/${encodeURIComponent(d.id)}/yaml`);
    editing = d.id;
    pick.reset();
    heading.textContent = `изменение: «${d.title}»`;
    saveBtn.lastChild.textContent = "Сохранить изменения";
    await setText(r.yaml);
    area.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "center" });
    area.focus();
  });
  const template = button("Взять шаблон", () => guarded(async () => {
    editing = null; heading.textContent = "новый тип"; pick.reset(); saveBtn.lastChild.textContent = "Добавить тип";
    await setText((await api("GET", "/api/admin/doctypes/template")).yaml);
  }), "small", "doc");
  const clearBtn = button("Очистить", reset, "small", "x");
  const tools = el("div", "form-actions");
  tools.append(template, clearBtn);
  // пока кнопка не активна, под ней сказано, чего не хватает (как на вкладке «База знаний»)
  const hint = el("span", "hint", HINT_EMPTY);
  const actions = el("div", "form-actions block");
  actions.append(saveBtn, hint);
  right.appendChild(sheet("Добавить или изменить тип", heading, pick.node, field("Описание (YAML)", area, "t-yaml", "— можно править прямо здесь"), verdict, tools, actions,
    el("p", "sheet-note", "Новый тип появляется на столе сразу, без перезапуска. Неверное описание не сохраняется: прежний файл остаётся как был.")));

  const listBody = el("div", "items");
  items.forEach((d, i) => {
    const it = el("div", "item");
    it.style.animationDelay = `${i * 50}ms`;
    const main = el("div");
    main.appendChild(el("h3", null, d.title));
    main.appendChild(el("p", "item-meta", `id «${d.id}» · файл ${d.source || "—"}`));
    const chips = el("div", "chips");
    chips.append(el("span", "chip", `${d.fields} ${plural(d.fields, ["поле", "поля", "полей"])}`));
    d.field_titles.slice(0, 5).forEach((t) => chips.appendChild(el("span", "chip soft", t)));
    if (d.field_titles.length > 5) chips.appendChild(el("span", "chip soft", `ещё ${d.field_titles.length - 5}`));
    main.appendChild(chips);
    const actions2 = el("div", "actions");
    const dl = el("a", null, null, { href: `/api/doctypes/${encodeURIComponent(d.id)}/yaml`, download: `${d.id}.yaml`, hidden: "" });
    actions2.append(
      button("Изменить", () => edit(d), "small", "doc"),
      moreMenu(`Действия с типом «${d.title}»`, [
        { text: "Изменить описание", icon: "doc", run: () => edit(d) },
        { text: "Скачать .yaml", icon: "download", run: () => dl.click() },
        { text: "Удалить тип", icon: "trash", danger: true, disabled: !d.deletable, run: () => guarded(async () => {
          if (!(await askConfirm({ title: `Удалить тип «${d.title}»?`, text: "Секретарь перестанет узнавать такие документы, а в списке на столе тип исчезнет. Файл описания будет удалён с сервера — скачайте его заранее, если он нужен." }))) return;
          await api("DELETE", `/api/admin/doctypes/${encodeURIComponent(d.id)}`);
          toast("Тип удалён");
          render();
        }) },
      ]),
    );
    it.append(main, actions2, dl);
    listBody.appendChild(it);
  });
  left.appendChild(sheet("Какие документы узнаёт секретарь", `${items.length}`, listBody));
}
