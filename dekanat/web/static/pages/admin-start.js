/* Кабинет сотрудника: запуск: вход, выход, показ кабинета. Подключается после admin.js (общие помощники и каркас) и common.js. */
"use strict";

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
  const submit = $("login-form").querySelector('button[type="submit"]');
  const idle = submit.textContent;
  submit.disabled = true;
  submit.textContent = "Входим…";
  try {
    await api("POST", "/api/admin/login", { username: $("u").value, password: $("p").value });
    $("p").value = "";
    $("p").type = "password";
    start();
  } catch (e) {
    showBanner($("banner"), "bad", e.message);
    $("login").animate && !REDUCED && $("login").animate([{ transform: "translateX(-6px)" }, { transform: "translateX(6px)" }, { transform: "none" }], { duration: 320 });
    $("p").focus();
    $("p").select();
  } finally {
    submit.disabled = false;
    submit.textContent = idle;
  }
});
// пароль: «глаз» внутри поля и предупреждение о Caps Lock
const loginPassword = passwordBox($("p"));
const caps = el("p", "caps-hint hidden", "Включён Caps Lock — пароль чувствителен к регистру.", { role: "status" });
loginPassword.after(caps);
$("p").addEventListener("keydown", (ev) => caps.classList.toggle("hidden", !(ev.getModifierState && ev.getModifierState("CapsLock"))));
$("p").addEventListener("keyup", (ev) => caps.classList.toggle("hidden", !(ev.getModifierState && ev.getModifierState("CapsLock"))));
$("p").addEventListener("blur", () => caps.classList.add("hidden"));
$("btn-logout").prepend(icon("logout"));
$("btn-logout").addEventListener("click", async () => {
  await api("POST", "/api/admin/logout").catch(() => null);
  showLogin("Вы вышли.");
});

start();
