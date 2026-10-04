import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { applyEdit, parseDraft } from "../draft";
import { readBinding, writeBinding } from "./MarkerField";
import { metricKind } from "./MetricEditor";
import { renameRefs } from "./refs";
import { parseWindow, writeWindow } from "./WindowField";

const example = readFileSync(resolve(__dirname, "../../../../../examples/sales/scenario.yaml"), "utf-8");

describe("формы конструктора", () => {
  it("привязка метки: короткие записи читаются и пишутся обратно", () => {
    expect(readBinding("revenue")).toEqual({ metric: "revenue" });
    expect(readBinding("period.Month")).toEqual({ period: "month", capital: true });
    expect(readBinding("period.year")).toEqual({ period: "year" });
    expect(readBinding(2026)).toEqual({ value: "2026" });
    for (const short of ["revenue", "period.Month", "period.year"]) expect(writeBinding(readBinding(short))).toBe(short);
    expect(writeBinding({ metric: "revenue", decimals: 1 })).toEqual({ metric: "revenue", decimals: 1 });
    expect(writeBinding({})).toBeUndefined();
  });

  it("окно данных: строки и словари", () => {
    expect(parseWindow(undefined)).toEqual({ type: "report_period", params: {} });
    expect(parseWindow("quarter_to_date")).toEqual({ type: "quarter_to_date", params: {} });
    expect(parseWindow("last_n(6)")).toEqual({ type: "last_n", params: { n: 6 } });
    expect(parseWindow("range(2026-01-01, 2026-03-31)")).toEqual({ type: "range", params: { start: "2026-01-01", end: "2026-03-31" } });
    expect(parseWindow({ type: "same_period_last_year" })).toEqual({ type: "same_period_last_year", params: {} });
    expect(writeWindow("report_period", {})).toBeUndefined();
    expect(writeWindow("last_n", { n: 3 })).toBe("last_n(3)");
    expect(writeWindow("range", { start: "2026-01-01", end: "2026-03-31" })).toEqual({ type: "range", start: "2026-01-01", end: "2026-03-31" });
  });

  it("вид показателя — по заданному полю", () => {
    expect(metricKind({ id: "a", input: "sales", fn: "sum" })).toBe("input");
    expect(metricKind({ id: "a", dataset: "by_month", fn: "sum" })).toBe("dataset");
    expect(metricKind({ id: "a", formula: "revenue / plan" })).toBe("formula");
    expect(metricKind({ id: "a", query: "SELECT 1" })).toBe("sql");
    expect(metricKind({ id: "a", code: "def value(tables, ctx): ..." })).toBe("python");
  });

  it("переименование id меняет ссылки на него", () => {
    const { doc, spec } = parseDraft(example);
    const out = applyEdit(example, doc, (d) => renameRefs(d, spec!, "input", "sales", "orders_in"));
    const next = parseDraft(out).spec!;
    const uses = [...next.datasets!, ...next.metrics!].filter((x) => x.input === "sales");
    expect(uses).toEqual([]);
    expect(next.datasets!.some((d) => d.input === "orders_in")).toBe(true);
    // id самого входа меняет конструктор отдельно
    expect(next.inputs![0].id).toBe("sales");

    const { doc: doc2, spec: spec2 } = parseDraft(example);
    const out2 = applyEdit(example, doc2, (d) => renameRefs(d, spec2!, "metric", "revenue", "sales_total"));
    expect(out2).not.toMatch(/: revenue\s*$/m);
  });
});
