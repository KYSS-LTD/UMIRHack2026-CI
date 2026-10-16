import { useEffect, useRef } from "react";
import { resetActivity, setActivity } from "../lib/activity";
import { STAGES, stageIndex } from "../lib/stages";
import type { Turn } from "./useDesk";

/** Ход проверки виден в верхней панели и в заголовке вкладки; при уходе со стола индикатор гаснет. */
export function useDeskActivity(turns: Turn[]): void {
  const wasBusy = useRef(false);
  const running = turns.find((t) => t.phase === "running" && !t.chat); // ответ анкеты — не проверка: панель и вкладка его не показывают
  const fraction = running?.progress?.fraction ?? 0;
  const label = running?.progress ? (STAGES[Math.min(stageIndex(running.progress.stage), STAGES.length - 1)]?.title ?? "Проверяю") : "";
  useEffect(() => {
    if (running) {
      wasBusy.current = true;
      setActivity({ busy: true, fraction, label });
    } else if (wasBusy.current) {
      wasBusy.current = false;
      setActivity({ busy: false, fraction: 1, label: "", finishedAt: Date.now() });
    }
  }, [!!running, fraction, label]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => resetActivity(), []);
}
