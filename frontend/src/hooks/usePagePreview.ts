import { useEffect, useState } from "react";
import { api } from "../api";
import { isPdf } from "../lib/files";
import type { PagePreview } from "../types";

/**
 * Настоящий первый лист PDF, пока документ лежит на столе и проверяется: у PDF нет картинки для показа, и без этого вместо документа
 * видна заглушка «PDF». У изображений предпросмотр и так есть (useObjectUrl) — для них хук ничего не загружает.
 * Если предпросмотр не получился, остаётся заглушка: проверка от него не зависит.
 */
export function usePagePreview(file: Blob | null | undefined, name = ""): PagePreview | null {
  const [preview, setPreview] = useState<{ file: Blob; page: PagePreview } | null>(null);

  useEffect(() => {
    if (!file || !isPdf({ name: name || (file as File).name || "", type: file.type })) return;
    const ctl = new AbortController();
    api
      .previewPage(file, name || (file as File).name || "document", ctl.signal)
      .then((page) => setPreview({ file, page }))
      .catch(() => undefined);
    return () => ctl.abort();
  }, [file, name]);

  return preview && preview.file === file ? preview.page : null;
}
