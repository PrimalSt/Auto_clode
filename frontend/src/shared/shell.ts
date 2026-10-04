// Мост к оболочке: перетаскивание файлов в окно. Перетащенные файлы получает тот, кто подписался
// последним (открытое поверх страницы окно диалога, а не страница под ним).
import { useEffect, useRef, useState } from "react";

type Target = { take: (paths: string[]) => void; hover: (over: boolean) => void };

const targets: Target[] = [];
let wired = false;

function wire(): void {
  const bridge = window.__AGEN__;
  if (wired || !bridge?.onDrop) return;
  wired = true;
  bridge.onDrop((paths) => targets.at(-1)?.take(paths));
  bridge.onDragHover?.((over) => targets.at(-1)?.hover(over));
}

/** Принимать файлы, перетащенные в окно оболочки. Возвращает, держат ли сейчас файлы над окном
 * (для подсветки) и можно ли вообще перетаскивать (в браузере — нельзя: он не отдаёт пути). */
export function useFileDrop(onPaths: (paths: string[]) => void, enabled = true): { over: boolean; supported: boolean } {
  const [over, setOver] = useState(false);
  const cb = useRef(onPaths);
  useEffect(() => {
    cb.current = onPaths;
  });
  const supported = !!window.__AGEN__?.onDrop;
  useEffect(() => {
    if (!enabled || !supported) return;
    wire();
    const t: Target = { take: (p) => cb.current(p), hover: setOver };
    targets.push(t);
    return () => {
      const i = targets.indexOf(t);
      if (i >= 0) targets.splice(i, 1);
    };
  }, [enabled, supported]);
  return { over: enabled && over, supported };
}

/** Расширения файлов выгрузок, которые читает приложение. */
export const EXPORT_EXTENSIONS = ["csv", "xlsx", "xls", "xlsb"];

/** Оставить только файлы с нужными расширениями (без точки, в любом регистре). */
export function withExtensions(paths: string[], extensions?: string[]): string[] {
  if (!extensions?.length) return paths;
  const ok = new Set(extensions.map((e) => e.toLowerCase().replace(/^\./, "")));
  return paths.filter((p) => ok.has(p.split(".").pop()?.toLowerCase() ?? ""));
}
