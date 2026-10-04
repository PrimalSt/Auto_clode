import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ShellBridge } from "./api/client";
import { useFileDrop, withExtensions } from "./shell";

/** Мост оболочки, как его добавляет окно Tauri: подписки и вызовы _drop/_hover. */
function fakeBridge() {
  const drop: ((p: string[]) => void)[] = [];
  const hover: ((o: boolean) => void)[] = [];
  const bridge: ShellBridge = {
    shell: true,
    onDrop: (cb) => {
      drop.push(cb);
      return () => undefined;
    },
    onDragHover: (cb) => {
      hover.push(cb);
      return () => undefined;
    },
  };
  return { bridge, drop: (p: string[]) => drop.forEach((f) => f(p)), hover: (o: boolean) => hover.forEach((f) => f(o)) };
}

describe("перетаскивание файлов в окно оболочки", () => {
  afterEach(() => {
    delete window.__AGEN__;
  });

  it("в браузере не поддерживается", () => {
    const { result } = renderHook(() => useFileDrop(() => undefined));
    expect(result.current).toEqual({ over: false, supported: false });
  });

  it("файлы получает последний подписчик, а после его ухода — предыдущий", () => {
    const shell = fakeBridge();
    window.__AGEN__ = shell.bridge;
    const page: string[][] = [];
    const dialog: string[][] = [];
    const a = renderHook(() => useFileDrop((p) => page.push(p)));
    const b = renderHook(() => useFileDrop((p) => dialog.push(p)));
    act(() => shell.hover(true));
    expect(b.result.current.over).toBe(true);
    expect(a.result.current.over).toBe(false);
    act(() => shell.drop(["C:\\a.csv"]));
    expect(dialog).toEqual([["C:\\a.csv"]]);
    expect(page).toEqual([]);
    b.unmount();
    act(() => shell.drop(["C:\\b.xlsx"]));
    expect(page).toEqual([["C:\\b.xlsx"]]);
    a.unmount();
  });

  it("отбирает файлы по расширению", () => {
    expect(withExtensions(["a.CSV", "b.pptx", "c.xlsb", "noext"], ["csv", ".xlsb"])).toEqual(["a.CSV", "c.xlsb"]);
    expect(withExtensions(["x.bin"])).toEqual(["x.bin"]);
  });
});
