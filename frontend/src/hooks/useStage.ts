import { useMemo } from "react";
import type { DeskDoc } from "../components/desk/highlight";
import { fieldBoxes } from "../lib/format";
import { STAGES, stageIndex } from "../lib/stages";
import { stageTurnOf, type Turn } from "./useDesk";
import { useObjectUrl } from "./useObjectUrl";
import { usePagePreview } from "./usePagePreview";

/**
 * Что лежит на столе: идущая или выбранная проверка, иначе файл из поля ввода, иначе пустой лоток.
 * Всё вычисляется из реплик и файла поля ввода — своего состояния, кроме предпросмотра файла, здесь нет.
 */
export function useStage(turns: Turn[], deskTurn: Turn | null, file: File | null) {
  const stageTurn = stageTurnOf(turns, deskTurn);
  const composerFileIsNew = !!file && (!stageTurn || stageTurn.file !== file);
  const subjectFile: Blob | null = composerFileIsNew ? file : (stageTurn?.file ?? null);
  const subjectName = composerFileIsNew ? (file?.name ?? "") : (stageTurn?.fileName ?? "");
  const subjectResult = composerFileIsNew ? null : (stageTurn?.phase === "done" ? stageTurn.result : null);
  const subjectUrl = useObjectUrl(subjectFile, subjectName);
  const pdfPreview = usePagePreview(subjectFile, subjectName);
  // пока идёт повторная проверка того же файла, на столе остаётся страница из прошлого результата, а не заглушка (рамки при этом не показываются)
  const lastPages = useMemo(() => (subjectFile ? ([...turns].reverse().find((t) => t.file === subjectFile && t.phase === "done" && t.result?.images?.length)?.result?.images ?? null) : null), [turns, subjectFile]);
  const scanning = !composerFileIsNew && stageTurn?.phase === "running";
  const allBoxes = useMemo(() => (subjectResult ? subjectResult.fields.flatMap(fieldBoxes) : []), [subjectResult]);

  // все документы, которые клали на стол в этой сессии, плюс файл из поля ввода
  const docs = useMemo<DeskDoc[]>(() => {
    const list: DeskDoc[] = [];
    const seen = new Set<Blob>();
    for (const t of turns) {
      if (t.file && !seen.has(t.file)) {
        seen.add(t.file);
        list.push({ key: t.id, file: t.file, name: t.fileName });
      }
    }
    if (file && !seen.has(file)) list.push({ key: "new", file, name: file.name });
    return list;
  }, [turns, file]);

  return {
    stageTurn,
    composerFileIsNew,
    subjectFile,
    subjectName,
    subjectResult,
    subjectUrl,
    pdfPreview,
    lastPages,
    hasSubject: !!subjectFile || !!subjectResult,
    scanning: !!scanning,
    progress: stageTurn?.progress?.fraction ?? 0,
    stageLabel: stageTurn?.progress ? (STAGES[Math.min(stageIndex(stageTurn.progress.stage), STAGES.length - 1)]?.title ?? "") : "",
    allBoxes,
    docs,
    activeDoc: docs.findIndex((d) => d.file === subjectFile),
  };
}
