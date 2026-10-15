/* Кабинет сотрудника: вкладка «Аккаунт»: смена пароля. Подключается после admin.js (общие помощники и каркас) и common.js. */
"use strict";

// ---------------------------------------------------------------- аккаунт
async function viewAccount(panel) {
  const cur = el("input", null, null, { type: "password", id: "pw-cur", autocomplete: "current-password" });
  const nw = el("input", null, null, { type: "password", id: "pw-new", autocomplete: "new-password" });
  const curBox = passwordBox(cur);
  const nwBox = passwordBox(nw);
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
    "Каждый неудачный вход записывается в историю изменений.", "Вход, смена пароля и все правки попадают в историю."]) rules.appendChild(el("li", null, text));
  const grid = el("div", "grid-2 even");
  grid.append(sheet("Смена пароля", null, field("Текущий пароль", curBox, "pw-cur"), field("Новый пароль", nwBox, "pw-new"), actions), sheet("Как защищён кабинет", null, rules));
  panel.appendChild(grid);
}
