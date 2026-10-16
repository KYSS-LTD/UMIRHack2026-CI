import { useEffect, useRef } from "react";

/** Крошечная шина событий: палитра команд и стол общаются без передачи пропсов через всё дерево. */
const target = new EventTarget();

export type BusEvent = "sample" | "new-desk" | "pick-file" | "focus-question" | "help" | "say" | "ask" | "forget" | "account" | "tour";

export function emit<T = undefined>(name: BusEvent, detail?: T): void {
  target.dispatchEvent(new CustomEvent(name, { detail }));
}

export function useBus<T = undefined>(name: BusEvent, handler: (detail: T) => void): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const fn = (e: Event) => ref.current((e as CustomEvent<T>).detail);
    target.addEventListener(name, fn);
    return () => target.removeEventListener(name, fn);
  }, [name]);
}
