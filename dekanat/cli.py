"""Командная строка: serve | bot | results | ask | batch | ocr | doctor | fetch-lang | validate-config | synth | eval | create-admin | reset-admin-password | template-add | template-sample | purge."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional


def _app():
    from .pipeline import Dekanat

    return Dekanat()


def cmd_serve(args) -> int:
    import uvicorn

    from .config import load_settings

    st = load_settings()
    host = args.host or st.get("server.host", "127.0.0.1")
    port = int(args.port or st.get("server.port", 8000))
    print(f"Онлайн-деканат: http://{host}:{port}")
    uvicorn.run("dekanat.web.app:create_app", host=host, port=port, factory=True, log_level="info")
    return 0


def cmd_bot(args) -> int:
    """Telegram-бот отдельным процессом (когда telegram.run_in_server: false) или разовая настройка бота (--setup)."""
    from .config import load_settings
    from .telegram.api import TelegramError
    from .web.app import create_app

    st = load_settings()
    if not st.get("telegram.bot_token"):
        print("Токен бота не задан. Получите его у @BotFather и передайте переменной окружения DEKANAT_TELEGRAM_BOT_TOKEN (в файлы настроек его писать не нужно).")
        return 1
    if not st.get("telegram.enabled", False):
        print("Бот выключен. Включите: telegram.enabled: true в config/settings.yaml (или DEKANAT_TELEGRAM_ENABLED=true).")
        return 1
    tg = create_app(st).state.telegram
    try:
        me = tg.api.get_me()
    except TelegramError as exc:
        print(f"Не удалось подключиться к Telegram: {exc.description}")
        return 1
    tg.username = tg.username or str(me.get("username") or "")
    tg.bot.username = tg.username
    print(f"Бот: @{tg.username}")
    if args.setup:
        tg.bot.setup()
        print("Команды, описание и кнопка меню обновлены." + ("" if tg.mini_app else " Кнопка приложения не добавлена: нужен https-адрес telegram.public_url."))
        return 0
    if st.get("telegram.run_in_server", True):
        print("Внимание: telegram.run_in_server: true — тот же бот запускается и вместе с сайтом; два процесса не должны опрашивать одного бота одновременно.")
    tg.start()
    print("Бот запущен. Остановка — Ctrl+C.")
    try:
        while tg.running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    tg.stop()
    return 0


def cmd_results(args) -> int:
    """results.json в формате организаторов (version 1.0): поля, предварительная проверка и ответы на вопросы набора."""
    import copy

    from .config import Settings, load_settings
    from .tools.official import OfficialExporter, check_report, write_results
    from .pipeline import Dekanat

    data = Path(args.data)
    manifest_path, questions_path = data / "documents.json", data / "questions.json"
    for need in (manifest_path, questions_path):
        if not need.exists():
            print(f"Не найден {need}. Укажите папку набора данных организаторов (--data): в ней documents.json, questions.json и documents/.", file=sys.stderr)
            return 1
    base = load_settings()
    raw = copy.deepcopy(base.raw)
    rules = Path(args.rules) if args.rules else data / "rules.md"
    if rules.exists():
        raw.setdefault("regulation", {})["path"] = str(rules.resolve())  # Положение набора вместо учебного: только на время этой команды
    app = Dekanat(Settings(raw=raw, root=base.root))
    print(f"Положение: {app.regulation_name} ({len(app.clauses)} пунктов). Документов в наборе: {len(json.loads(manifest_path.read_text(encoding='utf-8')))}")
    exp = OfficialExporter(app)
    t = time.time()
    report = exp.build(json.loads(manifest_path.read_text(encoding="utf-8")), json.loads(questions_path.read_text(encoding="utf-8")), data, args.pdf, args.limit)
    write_results(Path(args.out), report)
    for problem in check_report(report)[:10]:
        print("  замечание к формату:", problem)
    n_ans = sum(len(d["answers"]) for d in report["documents"])
    print(f"Готово: {args.out} — документов {len(report['documents'])}, ответов {n_ans}, {time.time() - t:.0f} с")
    return 0


def cmd_ask(args) -> int:
    from .export import result_record
    from .highlight import draw_overlay, overlay_items

    app = _app()
    path = Path(args.image)
    res = app.analyze(path.read_bytes(), path.name, args.question or "", args.type)
    if args.json:
        print(json.dumps(result_record(res, image=path.name, question=args.question or ""), ensure_ascii=False, indent=2))
    else:
        dt = res.doc_type
        print(f"Тип документа: {dt.title if dt else 'не определён'} ({res.how})")
        print("Карточка полей:")
        for f in res.fields:
            mark = {"found": "✓", "partial": "◐", "missing": "✗"}[f.status]
            print(f"  {mark} {f.title}: {f.value or 'не найдено'}" + (f"  [{', '.join(p.title + ('✓' if p.found else '✗') for p in f.parts)}]" if f.parts else ""))
        a = res.answer
        print(f"\nОтвет ({a.verdict}): {a.text}")
        for s in a.sources:
            print(f"  источник: {s.label}")
        for n in a.notes:
            print(f"  примечание: {n}")
        for w in res.warnings:
            print(f"  ! {w}")
        print(f"\nВремя: {res.timings}")
    if args.highlight and res.original_pages:
        import cv2

        for i, img in enumerate(res.original_pages):
            out = draw_overlay(img, overlay_items(res.fields, res.answer), i)
            target = Path(args.highlight) if len(res.original_pages) == 1 else Path(args.highlight).with_name(f"{Path(args.highlight).stem}_{i + 1}{Path(args.highlight).suffix}")
            ok, buf = cv2.imencode(target.suffix or ".png", out)
            target.write_bytes(buf.tobytes())
            print(f"Подсветка сохранена: {target}")
    return 0


def cmd_batch(args) -> int:
    from .export import read_batch_input, result_record

    app = _app()
    inp = Path(args.input)
    items = read_batch_input(inp)
    base = Path(args.images_dir) if args.images_dir else inp.parent
    if args.limit:
        items = items[: args.limit]
    results = []
    t0 = time.time()
    for n, it in enumerate(items, 1):
        data = None
        if it["image"]:
            p = Path(it["image"])
            cand = [p, base / p, base / "images" / p, base / p.name, base / "images" / p.name]
            found = next((c for c in cand if c.exists()), None)
            if found is None:
                print(f"[{n}/{len(items)}] {it['id']}: файл «{it['image']}» не найден, пропускаю", file=sys.stderr)
                results.append({"id": it["id"], "image": it["image"], "question": it["question"], "error": "файл не найден"})
                continue
            data = found.read_bytes()
        try:
            res = app.analyze(data, it["image"] or "", it["question"], it["doc_type"])
            rec = result_record(res, it["id"], it["image"], it["question"])
        except Exception as exc:  # один плохой файл не должен ронять весь прогон
            logging.exception("Ошибка обработки %s", it["id"])
            rec = {"id": it["id"], "image": it["image"], "question": it["question"], "error": str(exc)}
        results.append(rec)
        print(f"[{n}/{len(items)}] {it['id']}: {rec.get('verdict', 'ошибка')}", flush=True)
    out = Path(args.out)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Готово: {len(results)} записей -> {out} ({time.time() - t0:.0f} с)")
    return 0


def cmd_ocr(args) -> int:
    from .ocr import OcrOptions, OcrRunner, TesseractEngine
    from .ocr.preprocess import load_pages
    from .config import load_settings

    st = load_settings()
    runner = OcrRunner(TesseractEngine(st.get("ocr.tesseract_cmd"), str(st.get("ocr.languages", "rus+eng")), st.get("ocr.tessdata_dir")), OcrOptions())
    from .ocr.formats import check_format

    path = Path(args.image)
    data = path.read_bytes()
    check_format(data, path.name)
    for page in runner.run(load_pages(data, path.name)):
        q = {k: v for k, v in page.quality.items() if k not in ("tried", "photo")}
        print(f"--- страница {page.index + 1}: вариант {page.variant}, наклон {page.deskew_angle}°, качество {q}")
        print(page.text)
    return 0


def cmd_doctor(args) -> int:
    app = _app()
    info = app.info()
    ocr = info["ocr"]
    ok = True
    print("Диагностика окружения")
    print(f"  Python: {sys.version.split()[0]}")
    if ocr.get("available"):
        print(f"  Tesseract: {ocr['version']} ({ocr['cmd']}), языки rus/eng — есть")
    else:
        ok = False
        print(f"  Tesseract: ПРОБЛЕМА — {ocr.get('reason')}")
        if ocr.get("fix"):
            print(f"    Что делать: {ocr['fix']}")
    print(f"  Типы документов: {', '.join(d['title'] for d in info['doc_types'])}")
    print(f"  Положение: {info['regulation']['name'] or 'не загружено'} ({info['regulation']['clauses']} пунктов)")
    llm = info["llm"]
    if not llm.get("enabled"):
        print("  Локальная LLM: выключена (USE_LOCAL_LLM=false) — ни один компонент к модели не обращается")
    else:
        state = "доступна" if llm.get("available") else "НЕДОСТУПНА (приложение продолжит работать в режиме без LLM)"
        print(f"  Локальная LLM: включена — {llm['backend']}, модель {llm['model']}, только CPU={llm.get('cpu_only')}; {state}")
    tgs = app.settings
    if tgs.get("telegram.enabled", False):
        have = bool(tgs.get("telegram.bot_token"))
        https = str(tgs.get("telegram.public_url") or "").startswith("https://")
        print("  Telegram-бот: включён" + ("" if have else ", но токен не задан (DEKANAT_TELEGRAM_BOT_TOKEN)") + f"; режим {tgs.get('telegram.mode', 'polling')}"
              + ("; Mini App доступно" if have and https else "; Mini App выключено (нужен https-адрес telegram.public_url)"))
    else:
        print("  Telegram-бот: выключен (telegram.enabled: false) — сайт работает без него")
    from .storage import Storage

    storage = Storage(app.settings)
    print(f"  Администраторов: {storage.admins.count()}" + ("" if storage.admins.count() else " — создайте: python -m dekanat create-admin"))
    print(f"  Хранилище: {storage.db.path}")
    storage.close()
    return 0 if ok else 1


def cmd_fetch_lang(args) -> int:
    from pathlib import Path

    from .config import load_settings
    from .ocr import TesseractEngine
    from .ocr.langdata import LangDataError, MANUAL_PAGE, fetch_languages

    st = load_settings()
    dest = Path(st.root) / "data" / "tessdata"
    print(f"Скачиваю языковые данные Tesseract ({', '.join(args.langs)}) в {dest} …")
    try:
        result = fetch_languages(dest, args.langs, args.force)
    except (LangDataError, OSError) as exc:
        print(f"  Не получилось: {exc}")
        print(f"  Скачайте нужные файлы вручную со страницы {MANUAL_PAGE} и положите их в {dest}.")
        return 1
    for lang, what in result.items():
        print(f"  {lang}: {what}")
    engine = TesseractEngine(cmd=st.get("ocr.tesseract_cmd"), languages=str(st.get("ocr.languages", "rus+eng")), tessdata_dir=str(dest))
    info = engine.info(fresh=True)
    if info.get("available"):
        print("Готово: распознавание работает. Перезапустите сервер: python -m dekanat serve")
        return 0
    print(f"Файлы скачаны, но распознавание пока не работает: {info.get('reason')}")
    if info.get("fix"):
        print(f"  {info['fix']}")
    return 1


def cmd_create_admin(args) -> int:
    import os
    from getpass import getpass

    from .admin import AdminAuth
    from .config import load_settings
    from .storage import Storage

    st = load_settings()
    storage = Storage(st)
    username = args.username or input("Логин администратора: ").strip()
    password = os.environ.get(args.password_env) if args.password_env else None
    if password is None:
        password = getpass("Пароль: ")
        if password != getpass("Повторите пароль: "):
            print("Пароли не совпадают.", file=sys.stderr)
            return 1
    try:
        AdminAuth(storage, st).create_admin(username, password)
    except ValueError as exc:
        print(f"Администратор не создан: {exc}", file=sys.stderr)
        if "уже существует" in str(exc):
            print(f"Забыли пароль? Задайте новый: python -m dekanat reset-admin-password --username {username}", file=sys.stderr)
        return 1
    print(f"Администратор «{username}» создан. Вход: http://{st.get('server.host', '127.0.0.1')}:{st.get('server.port', 8000)}/admin")
    return 0


def cmd_reset_admin_password(args) -> int:
    """Новый пароль для существующего администратора (забытый пароль не восстановить: в базе только хэш). Все входы этой записи завершаются."""
    import os
    from getpass import getpass

    from .admin import AdminAuth
    from .config import load_settings
    from .storage import Storage

    st = load_settings()
    storage = Storage(st)
    admins = [a["username"] for a in storage.admins.list()]
    if not admins:
        print("Администраторов нет. Создайте: python -m dekanat create-admin", file=sys.stderr)
        return 1
    username = args.username or input(f"Логин администратора ({', '.join(admins)}): ").strip()
    password = os.environ.get(args.password_env) if args.password_env else None
    if password is None:
        password = getpass("Новый пароль: ")
        if password != getpass("Повторите пароль: "):
            print("Пароли не совпадают.", file=sys.stderr)
            return 1
    try:
        AdminAuth(storage, st).reset_password(username, password)
    except ValueError as exc:
        print(f"Пароль не изменён: {exc}", file=sys.stderr)
        if admins:
            print(f"Существующие учётные записи: {', '.join(admins)}.", file=sys.stderr)
        return 1
    print(f"Пароль администратора «{username}» изменён, прежние входы завершены. Вход: http://{st.get('server.host', '127.0.0.1')}:{st.get('server.port', 8000)}/admin")
    return 0


def cmd_template_add(args) -> int:
    from .config import load_settings
    from .templates import TemplateError, convert_to_template
    from .storage import Storage

    storage = Storage(load_settings())
    try:
        info = convert_to_template(args.file, storage=storage, template_id=args.id, name=args.name, overwrite=args.replace, actor="cli")
    except TemplateError as exc:
        print(f"Шаблон не добавлен: {exc}", file=sys.stderr)
        return 1
    storage.audit.log("cli", "template.replace" if info.version > 1 else "template.create", "template", info.template_id, {"name": info.name, "version": info.version, "placeholders": info.keys})
    print(f"Шаблон «{info.name}» сохранён: id={info.template_id}, версия {info.version}")
    for p in info.placeholders:
        print(f"  {{{{ {p.key} }}}} — {p.count} раз(а), {', '.join(p.locations)}" + ("" if p.known else "  [свой ключ]"))
    if not info.placeholders:
        print("  Внимание: в файле не найдено ни одного плейсхолдера вида {{ ключ }}.")
    return 0


def cmd_template_sample(args) -> int:
    from .templates.sample import build_enrollment_template, build_sample_template

    build, default_name = (build_enrollment_template, "zayavlenie_o_zachislenii.docx") if args.kind == "enrollment" else (build_sample_template, "zayavlenie_o_perevode.docx")
    print(f"Учебный шаблон записан: {build(args.out or default_name)}")
    return 0


def cmd_purge(args) -> int:
    from .config import load_settings
    from .storage import Storage

    result = Storage(load_settings()).purge()
    print("Удалено по сроку хранения: " + ", ".join(f"{k}={v}" for k, v in result.items()))
    return 0


def cmd_validate(args) -> int:
    from .config import ConfigError, load_doc_types, load_settings

    st = load_settings()
    try:
        types = load_doc_types(Path(args.dir) if args.dir else st.doc_types_dir)
    except ConfigError as exc:
        print(f"ОШИБКА КОНФИГА: {exc}", file=sys.stderr)
        return 1
    for t in types.values():
        units = sum(len(f.parts) or 1 for f in t.fields)
        print(f"  ✓ {t.id}: «{t.title}», полей {len(t.fields)} (проверяемых единиц {units})")
    print("Конфиг корректен.")
    return 0


def cmd_synth(args) -> int:
    from .tools import synth

    return synth.main(args.rest)


def cmd_eval(args) -> int:
    from .tools.evaluate import print_report, run_eval, run_qa_oracle

    app = _app()
    if args.oracle:
        rep = run_qa_oracle(app, Path(args.data), args.verbose, Path(args.out) if args.out else None)
    else:
        rep = run_eval(app, Path(args.data), args.limit, args.verbose, Path(args.out) if args.out else None)
    print_report(rep)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dekanat", description="Онлайн-деканат: цифровой секретарь")
    p.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="запустить веб-интерфейс")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(func=cmd_serve)

    bt = sub.add_parser("bot", help="запустить Telegram-бота отдельным процессом или настроить его (--setup)")
    bt.add_argument("--setup", action="store_true", help="только записать команды, описание и кнопку меню бота и выйти")
    bt.set_defaults(func=cmd_bot)

    rs = sub.add_parser("results", help="results.json в формате организаторов кейса по набору данных (documents.json, questions.json, rules.md)")
    rs.add_argument("--data", required=True, help="папка набора данных организаторов")
    rs.add_argument("--out", default="results.json")
    rs.add_argument("--rules", help="Положение (по умолчанию <data>/rules.md)")
    rs.add_argument("--pdf", action="store_true", help="брать PDF-версии документов вместо PNG")
    rs.add_argument("--limit", type=int, help="только первые N документов (для быстрой проверки)")
    rs.set_defaults(func=cmd_results)

    a = sub.add_parser("ask", help="проверить документ и ответить на вопрос")
    a.add_argument("image", help="JPG/PNG/PDF")
    a.add_argument("question", nargs="?", default="")
    a.add_argument("--type", help="тип документа (по умолчанию определяется автоматически)")
    a.add_argument("--json", action="store_true")
    a.add_argument("--highlight", help="сохранить снимок с подсветкой найденных полей")
    a.set_defaults(func=cmd_ask)

    b = sub.add_parser("batch", help="прогнать набор документов и вопросов -> results.json")
    b.add_argument("--input", required=True, help="JSON/JSONL/CSV с полями id, image, question[, doc_type]")
    b.add_argument("--out", default="results.json")
    b.add_argument("--images-dir")
    b.add_argument("--limit", type=int)
    b.set_defaults(func=cmd_batch)

    o = sub.add_parser("ocr", help="показать результат OCR")
    o.add_argument("image")
    o.set_defaults(func=cmd_ocr)

    d = sub.add_parser("doctor", help="проверить окружение")
    d.set_defaults(func=cmd_doctor)

    fl = sub.add_parser("fetch-lang", help="скачать языковые данные Tesseract (rus, eng, osd) в data/tessdata — если установщик Tesseract поставил только английский")
    fl.add_argument("langs", nargs="*", default=["rus", "eng", "osd"], help="какие языки скачать (по умолчанию rus eng osd)")
    fl.add_argument("--force", action="store_true", help="скачать заново, даже если файл уже есть")
    fl.set_defaults(func=cmd_fetch_lang)

    v = sub.add_parser("validate-config", help="проверить YAML типов документов")
    v.add_argument("--dir")
    v.set_defaults(func=cmd_validate)

    ca = sub.add_parser("create-admin", help="создать учётную запись администратора (пароль вводится скрытно или берётся из переменной окружения)")
    ca.add_argument("--username")
    ca.add_argument("--password-env", help="имя переменной окружения с паролем (пароль в аргументах командной строки не принимается)")
    ca.set_defaults(func=cmd_create_admin)

    rp = sub.add_parser("reset-admin-password", help="задать новый пароль существующему администратору (если пароль забыт; пароль вводится скрытно или берётся из переменной окружения)")
    rp.add_argument("--username")
    rp.add_argument("--password-env", help="имя переменной окружения с новым паролем (в аргументах командной строки пароль не принимается)")
    rp.set_defaults(func=cmd_reset_admin_password)

    ta = sub.add_parser("template-add", help="добавить DOCX-шаблон с плейсхолдерами {{ ключ }}")
    ta.add_argument("file")
    ta.add_argument("--id", help="идентификатор шаблона (по умолчанию создаётся уникальный)")
    ta.add_argument("--name", help="название шаблона")
    ta.add_argument("--replace", action="store_true", help="явно разрешить замену существующего шаблона с этим --id")
    ta.set_defaults(func=cmd_template_add)

    ts = sub.add_parser("template-sample", help="записать учебный образец шаблона: «Заявление о переводе» (по умолчанию) или «Заявление о зачислении» (--kind enrollment)")
    ts.add_argument("out", nargs="?", default=None)
    ts.add_argument("--kind", choices=("transfer", "enrollment"), default="transfer", help="какой образец записать")
    ts.set_defaults(func=cmd_template_sample)

    pg = sub.add_parser("purge", help="удалить просроченные данные (сессии, готовые документы, старые обращения) по настройкам privacy.*")
    pg.set_defaults(func=cmd_purge)

    y = sub.add_parser("synth", help="сгенерировать синтетические документы (см. dekanat/tools/synth.py)")
    y.add_argument("rest", nargs=argparse.REMAINDER)
    y.set_defaults(func=cmd_synth)

    e = sub.add_parser("eval", help="оценка качества на синтетическом наборе")
    e.add_argument("--data", default="data/synthetic")
    e.add_argument("--limit", type=int)
    e.add_argument("--out", help="сохранить подробный отчёт в JSON")
    e.add_argument("--verbose", action="store_true", help="показывать ошибочные ответы")
    e.add_argument("--oracle", action="store_true", help="только логика ответов: поля берутся из эталона, OCR не запускается")
    e.set_defaults(func=cmd_eval)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "synth":  # аргументы генератора передаём как есть
        from .tools import synth

        return synth.main(raw[1:])
    args = build_parser().parse_args(raw)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
