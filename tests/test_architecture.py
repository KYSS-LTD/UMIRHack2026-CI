"""Границы между слоями (см. docs/ARCHITECTURE.md): зависимости идут сверху вниз, ядро не знает про сайт и Telegram, циклов нет.

Тест читает исходники, ничего не запускает: нарушение видно сразу, а не через полгода, когда «маленький импорт» превратится в клубок.
"""

import ast
from collections import defaultdict
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "dekanat"

# слой -> пакеты и модули верхнего уровня. Импортировать можно свой слой и нижележащие, не выше.
LAYERS = [
    ("foundation", {"models", "config", "textutil"}),
    ("document", {"ocr", "extract", "classify", "highlight"}),
    ("knowledge", {"knowledge_base", "qa", "llm"}),
    ("pipeline", {"pipeline", "export"}),  # export — запись результата проверки в JSON: часть конвейера, её используют и сайт, и пакетный режим
    ("services", {"dialog", "storage", "templates", "accounts", "mailer", "assistant", "doctypes"}),
    ("adapters", {"web", "telegram", "admin"}),
    ("tools", {"cli", "tools", "__main__"}),
]
RANK = {name: i for i, (_layer, names) in enumerate(LAYERS) for name in names}
LAYER_OF = {name: layer for layer, names in LAYERS for name in names}
WEB_ONLY = {"fastapi", "starlette"}  # HTTP-фреймворк нужен только сайту и кабинету; ядро и бот без него


def _imports():
    """(пакет-источник, пакет-цель, файл, внутри ли функции) для каждого импорта внутри dekanat."""
    found = []
    for path in PKG.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(PKG)
        me = rel.parts[0] if len(rel.parts) > 1 else rel.stem
        tree = ast.parse(path.read_text(encoding="utf-8"))
        inside = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                inside[id(child)] = isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) or inside.get(id(parent), False)
        for node in ast.walk(tree):
            target = None
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                base = list(rel.parent.parts)
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                parts = base + [m for m in (node.module or "").split(".") if m]
                target = parts[0] if parts else None  # «from .. import __version__» — корень пакета, не зависимость
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("dekanat."):
                target = node.module.split(".")[1]
            if target and target != me:
                found.append((me, target, str(rel), inside.get(id(node), False)))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for name in names:
                found.append((me, "ext:" + name.split(".")[0], str(rel), False))
    return found


IMPORTS = _imports()
INTERNAL = [(a, b, f, lazy) for a, b, f, lazy in IMPORTS if not b.startswith("ext:")]


def test_every_package_belongs_to_a_layer():
    known = {a for a, _b, _f, _l in INTERNAL} | {b for _a, b, _f, _l in INTERNAL}
    sources = {p.relative_to(PKG).parts[0].removesuffix(".py") for p in PKG.iterdir() if p.name != "__pycache__" and (p.suffix == ".py" or p.is_dir())}
    missing = (known | sources) - set(RANK) - {"__init__"}
    assert not missing, f"Новый пакет или модуль нужно отнести к слою в tests/test_architecture.py и docs/ARCHITECTURE.md: {sorted(missing)}"


def test_dependencies_point_downwards():
    bad = sorted({f"{f}: {a} ({LAYER_OF[a]}) импортирует {b} ({LAYER_OF[b]})" for a, b, f, _lazy in INTERNAL if a in RANK and b in RANK and RANK[a] < RANK[b]})
    assert not bad, "Нижний слой не должен знать про верхний:\n" + "\n".join(bad)


@pytest.mark.parametrize("source, forbidden", [
    ("telegram", {"web", "admin"}),  # бот и сайт общаются через ядро ассистента, а не друг через друга
    ("admin", {"web", "telegram"}),
    ("assistant", {"web", "telegram", "admin"}),
])
def test_adapters_do_not_depend_on_each_other(source, forbidden):
    bad = sorted({f"{f}: {a} -> {b}" for a, b, f, _lazy in INTERNAL if a == source and b in forbidden})
    assert not bad, "\n".join(bad)


def test_http_framework_stays_in_the_web_adapters():
    bad = sorted({f"{f}: {a} импортирует {b[4:]}" for a, b, f, _lazy in IMPORTS if b.startswith("ext:") and b[4:] in WEB_ONLY and a not in {"web", "admin"} and a != "__main__"})
    assert not bad, "HTTP-фреймворк в ядре или в боте:\n" + "\n".join(bad)


def test_there_are_no_import_cycles_between_packages():
    graph = defaultdict(set)
    for a, b, _f, _lazy in INTERNAL:
        if a not in {"cli", "__main__"}:  # cli — корень сборки: он подключает всё и сам никем не импортируется
            graph[a].add(b)
    cycles, state, stack = [], {}, []

    def dfs(n):
        state[n] = 1
        stack.append(n)
        for m in sorted(graph.get(n, ())):
            if state.get(m) == 1:
                cycles.append(" -> ".join(stack[stack.index(m):] + [m]))
            elif state.get(m) is None:
                dfs(m)
        stack.pop()
        state[n] = 2

    for n in sorted(graph):
        if state.get(n) is None:
            dfs(n)
    assert not cycles, "Циклы между пакетами:\n" + "\n".join(cycles)
