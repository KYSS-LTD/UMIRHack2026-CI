import { useEffect, useState } from "react";
import type { Verdict } from "../../types";
import { Stamp } from "../../ui/Stamp";

const SCENES: { verdict: Verdict; q: string; fields: [string, boolean][] }[] = [
  { verdict: "ok", q: "Хватит ли этой справки для перевода на 2 курс?", fields: [["ФИО", true], ["Вуз", true], ["Период", true], ["Печать", true]] },
  { verdict: "missing", q: "Примет ли деканат моё заявление на академ?", fields: [["ФИО", true], ["Причина", true], ["Дата", false], ["Подпись", false]] },
  { verdict: "no_answer", q: "Сколько стоит обучение?", fields: [["ФИО", true], ["Группа", true], ["Курс", true], ["Номер", true]] },
];

/** Зацикленная мини-сцена: лист → луч → поля → штамп. Показывает три вида ответов из ТЗ, пока стол пуст. */
export function DemoLoop() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setI((n) => (n + 1) % SCENES.length), 5200);
    return () => clearInterval(id);
  }, []);
  const scene = SCENES[i]!;
  return (
    <div className="demo" aria-hidden="true">
      <div className="demo-paper" key={`p${i}`}>
        <span className="demo-line w80" />
        <span className="demo-line w55" />
        <span className="demo-line w90" />
        <span className="demo-line w40" />
        <span className="demo-line w70" />
        <span className="demo-beam" />
        <div className="demo-fields">
          {scene.fields.map(([name, ok], k) => (
            <span key={name} className={`demo-field ${ok ? "ok" : "miss"}`} style={{ animationDelay: `${1.2 + k * 0.22}s` }}>
              {ok ? "✓" : "–"} {name}
            </span>
          ))}
        </div>
      </div>
      <div className="demo-side" key={`s${i}`}>
        <p className="demo-q">{scene.q}</p>
        <div className="demo-stamp">
          <Stamp verdict={scene.verdict} />
        </div>
      </div>
    </div>
  );
}
