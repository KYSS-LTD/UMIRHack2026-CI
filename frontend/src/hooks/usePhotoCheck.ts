import { useEffect, useState } from "react";
import { api } from "../api";
import { isPdf } from "../lib/files";
import type { PhotoQuality } from "../types";

/**
 * Быстрая проверка снимка сразу после выбора файла, до отправки: если он явно плох (размытый, тёмный, мелкий), студент узнаёт об этом
 * сразу и может переснять, не дожидаясь проверки. Предупреждение только о том, что точно стоит исправить; подробная оценка с учётом
 * распознавания придёт вместе с ответом. PDF не проверяем, ошибки проверки не показываем: она ничего не блокирует.
 */
export function usePhotoCheck(file: File | null): PhotoQuality | null {
  const [found, setFound] = useState<{ file: File; quality: PhotoQuality } | null>(null);

  useEffect(() => {
    if (!file || isPdf({ name: file.name, type: file.type })) return;
    const ctl = new AbortController();
    api
      .photoQuality(file, file.name, ctl.signal)
      .then((quality) => quality.retake && setFound({ file, quality }))
      .catch(() => undefined);
    return () => ctl.abort();
  }, [file]);

  return found && found.file === file ? found.quality : null;
}
