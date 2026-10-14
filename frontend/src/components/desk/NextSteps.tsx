import { useEffect, useState } from "react";
import { api } from "../../api";
import type { NextSteps as NextStepsData } from "../../types";

const longDate = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" }).replace(/\s*г\.$/, ""); // «28 октября 2026», без «г.»

/**
 * «Что дальше» после готового заявления: что принести и к какому сроку ждать решения. Берётся из действующего Положения со ссылкой на пункт;
 * если в Положении об этом ничего нет (например, о зачислении), блок не показывается — ничего не выдумываем.
 */
export function NextSteps() {
  const [steps, setSteps] = useState<NextStepsData | null>(null);

  useEffect(() => {
    let alive = true;
    api.assistant
      .nextSteps()
      .then((res) => alive && setSteps(res))
      .catch(() => undefined); // подсказка необязательна: без неё всё остальное работает
    return () => {
      alive = false;
    };
  }, []);

  if (!steps || (!steps.documents && !steps.deadline)) return null;
  const { documents, deadline } = steps;
  return (
    <aside className="next-steps" aria-label="Что дальше">
      <p className="eyebrow">Что дальше</p>
      {documents && (
        <p>
          <b>Что понадобится.</b> {documents.text}
        </p>
      )}
      {deadline && (
        <p>
          <b>Срок решения.</b> Ориентировочно до {longDate(deadline.date)}: {deadline.days} {deadline.working ? "рабочих" : "календарных"} дней по {deadline.source} Положения
          {deadline.working ? ", без учёта праздников" : ""}. Окончательное решение принимает сотрудник деканата.
        </p>
      )}
    </aside>
  );
}
