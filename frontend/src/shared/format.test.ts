import { describe, expect, it } from "vitest";
import { bytes, fileName, periodKey, periodLabel, plural } from "./format";

const p = (start: string, end_exclusive: string, unit: string) => ({ start, end_exclusive, unit }) as never;

describe("периоды", () => {
  it("подписи", () => {
    expect(periodLabel(p("2026-03-01", "2026-04-01", "month"))).toBe("март 2026");
    expect(periodLabel(p("2026-01-01", "2026-04-01", "quarter"))).toBe("1 кв. 2026");
    expect(periodLabel(p("2026-03-03", "2026-03-20", "range"))).toBe("03.03.2026 – 19.03.2026");
    expect(periodLabel(null)).toBe("—");
  });
  it("запись для сервера", () => {
    expect(periodKey(p("2026-03-01", "2026-04-01", "month"))).toBe("2026-03");
    expect(periodKey(p("2026-04-01", "2026-07-01", "quarter"))).toBe("2026-Q2");
    expect(periodKey(p("2026-03-03", "2026-03-20", "range"))).toBe("2026-03-03..2026-03-19");
  });
});

describe("числа и слова", () => {
  it("склонение", () => {
    expect([1, 2, 5, 11, 21, 22, 112].map((n) => plural(n, "строка", "строки", "строк"))).toEqual([
      "строка", "строки", "строк", "строк", "строка", "строки", "строк",
    ]);
  });
  it("размеры и имена файлов", () => {
    expect(bytes(512)).toBe("512 Б");
    expect(bytes(1536).replace(/\s/g, " ")).toBe("1,5 КБ");
    expect(fileName("C:\\Выгрузки\\Продажи.csv")).toBe("Продажи.csv");
    expect(fileName("/home/u/a.xlsx")).toBe("a.xlsx");
  });
});
