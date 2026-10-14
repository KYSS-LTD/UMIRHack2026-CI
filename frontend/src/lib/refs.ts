/** Ссылки внутри текста Положения: «п. 3.4», «пункта 7.2», «пунктом 2.1» становятся переходом к этому пункту. */
// \b в JavaScript знает только латиницу, поэтому граница слова — «перед этим не буква»
const REF = /(?<![\p{L}])(?:п\.|пункт(?:а|е|у|ом|ы|ов)?)\s*(\d+(?:\.\d+)+)/giu;

export type RefPart = string | { ref: string; text: string };

/**
 * Делит текст на обычные куски и ссылки на пункты. Ссылкой становится только упоминание пункта, который есть в Положении (known):
 * «раздела 3» (без точки) и несуществующие номера остаются обычным текстом.
 */
export function linkRefs(text: string, known: ReadonlySet<string>): RefPart[] {
  const out: RefPart[] = [];
  let last = 0;
  for (const m of text.matchAll(REF)) {
    const ref = m[1]!;
    if (!known.has(ref)) continue;
    const start = m.index ?? 0;
    if (start > last) out.push(text.slice(last, start));
    out.push({ ref, text: m[0] });
    last = start + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out.length ? out : [text];
}
