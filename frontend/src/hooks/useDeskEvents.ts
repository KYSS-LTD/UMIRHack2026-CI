import { useEffect, useRef, useState } from "react";
import { fileFromClipboard, validateFile } from "../lib/files";
import { useToast } from "../ui/Toasts";

interface Handlers {
  busy: boolean;
  maxMb: number;
  /** файл брошен на стол или вставлен из буфера */
  onFile: (f: File) => void;
  onCancel: () => void;
  onFocusQuestion: () => void;
}

/**
 * События всего окна: файл можно бросить на любое место стола и вставить из буфера, Esc отменяет проверку, «/» переводит к вопросу.
 * Возвращает true, пока над окном тащат файл (для подсказки «Отпустите — положу на стол»).
 */
export function useDeskEvents(h: Handlers): boolean {
  const toast = useToast();
  const [dragging, setDragging] = useState(false);
  const latest = useRef(h);
  latest.current = h;

  useEffect(() => {
    let depth = 0;
    const hasFiles = (e: DragEvent) => !!e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files");
    const onEnter = (e: DragEvent) => hasFiles(e) && (depth++, setDragging(true));
    const onLeave = (e: DragEvent) => hasFiles(e) && (depth = Math.max(0, depth - 1)) === 0 && setDragging(false);
    const onOver = (e: DragEvent) => hasFiles(e) && e.preventDefault();
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setDragging(false);
      const f = e.dataTransfer?.files[0];
      const { busy, maxMb, onFile } = latest.current;
      if (!f || busy) return;
      const problem = validateFile(f, maxMb);
      problem ? toast.show(problem, "error") : onFile(f);
    };
    const onPaste = (e: ClipboardEvent) => {
      const f = fileFromClipboard(e);
      if (f && !latest.current.busy) (latest.current.onFile(f), toast.show(`Вставлено из буфера: ${f.name}`, "info"));
    };
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
      if (e.key === "Escape" && latest.current.busy) latest.current.onCancel();
      if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey) (e.preventDefault(), latest.current.onFocusQuestion());
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("dragover", onOver);
    window.addEventListener("drop", onDrop);
    window.addEventListener("paste", onPaste);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("paste", onPaste);
      window.removeEventListener("keydown", onKey);
    };
  }, [toast]);

  return dragging;
}
