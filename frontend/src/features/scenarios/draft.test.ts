import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { addIn, applyEdit, duplicateIn, freeId, moveIn, parseDraft, previewTarget, printDoc, renameKey, setIn, slug, useDraft } from "./draft";

const example = readFileSync(resolve(__dirname, "../../../../examples/sales/scenario.yaml"), "utf-8");

describe("черновик сценария", () => {
  it("правит одно значение, не трогая остальные строки", () => {
    const { doc, error } = parseDraft(example);
    expect(error).toBeNull();
    const out = applyEdit(example, doc, (d) => setIn(d, ["inputs", 0, "pipeline", 0, "keep"], "first"));
    const lines = example.split("\n");
    const changed = out.split("\n").filter((l, i) => l !== lines[i]);
    expect(changed).toEqual(["      - { id: dedupe_orders, type: dedupe, by: [order_no], keep: first }"]);
    expect(applyEdit(example, doc, () => {})).toBe(example);
  });

  it("добавляет и удаляет строки, сохраняя соседние как есть", () => {
    const { doc } = parseDraft(example);
    const out = applyEdit(example, doc, (d) => {
      addIn(d, ["inputs", 0, "pipeline"], { id: "only_moscow", type: "filter", where: "region = 'Москва'" });
      setIn(d, ["datasets", 1, "compare"], null);
    });
    expect(out).toContain("    sort: [-revenue]");
    expect(out).toContain("      - id: only_moscow\n        type: filter\n        where: region = 'Москва'\n");
    expect(out).not.toContain("compare: [previous_period]          #");
    const spec = parseDraft(out).spec!;
    expect(spec.inputs![0].pipeline!.length).toBe(4);
    expect(spec.datasets![1].compare).toBeUndefined();
    expect(printDoc(parseDraft(out).doc)).toBeTruthy();
  });

  it("пустое значение удаляет ключ, списки добавляются и переставляются", () => {
    const { doc } = parseDraft("name: x\ninputs:\n  - id: a\n    source: s\n    main: true\n");
    setIn(doc, ["inputs", 0, "main"], false);
    setIn(doc, ["inputs", 0, "main"], null);
    addIn(doc, ["inputs", 0, "pipeline"], { id: "d", type: "dedupe" });
    addIn(doc, ["inputs", 0, "pipeline"], { id: "f", type: "filter", where: "amount > 0" });
    moveIn(doc, ["inputs", 0, "pipeline"], 1, 0);
    renameKey(doc, [], "name", "name");
    const spec = parseDraft(printDoc(doc)).spec!;
    expect(spec.inputs![0].main).toBeUndefined();
    expect(spec.inputs![0].pipeline!.map((s) => s.id)).toEqual(["f", "d"]);
  });

  it("ошибку разбора показывает со строкой", () => {
    expect(parseDraft("name: [x\n").error).toMatch(/строка/);
    expect(parseDraft("- a\n").error).toMatch(/словарь/);
  });

  it("находит узел превью по месту в сценарии", () => {
    const { spec } = parseDraft(example);
    expect(previewTarget(spec, ["inputs", 0])).toEqual({ kind: "node", target: "input:sales" });
    expect(previewTarget(spec, ["inputs", 0, "pipeline", 1])).toEqual({ kind: "node", target: "input:sales/step:positive_only" });
    expect(previewTarget(spec, ["datasets", 0])).toEqual({ kind: "node", target: "dataset:by_month" });
    expect(previewTarget(spec, ["slides", 2])).toEqual({ kind: "slide", number: 3 });
    expect(previewTarget(spec, [])).toBeNull();
  });

  it("предлагает свободный id", () => {
    expect(freeId("dedupe", ["dedupe", "dedupe_2"])).toBe("dedupe_3");
    expect(slug("Выручка по регионам")).toBe("vyruchka_po_regionam");
    expect(slug("2026", "d")).toBe("d_2026");
  });

  it("дублирует элемент со свободным id сразу после него", () => {
    const { doc, spec } = parseDraft(example);
    let at = -1;
    const out = applyEdit(example, doc, (d) => {
      at = duplicateIn(d, ["inputs", 0], spec!.inputs!.map((x) => x.id));
    });
    const next = parseDraft(out).spec!;
    expect(at).toBe(1);
    expect(next.inputs!.map((x) => x.id)).toEqual(["sales", "sales_2", "plan"]);
    expect(next.inputs![1].main).toBeUndefined();
    expect(next.inputs![1].pipeline).toEqual(next.inputs![0].pipeline);
  });

  it("несохранённые правки переживают закрытие окна, пока сохранённая версия та же", () => {
    const st = useDraft.getState();
    st.load("s1", "name: a\n");
    st.setText("name: b\n");
    useDraft.setState({ scenarioId: null, text: "", base: "" });
    expect(useDraft.getState().load("s1", "name: a\n", true)).toBe(true);
    expect(useDraft.getState().text).toBe("name: b\n");
    // «Отменить правки» и сохранение забывают их
    useDraft.getState().load("s1", "name: a\n");
    expect(useDraft.getState().load("s1", "name: a\n", true)).toBe(false);
    // правки от другой версии не возвращаются
    useDraft.getState().setText("name: c\n");
    expect(useDraft.getState().load("s1", "name: z\n", true)).toBe(false);
    expect(useDraft.getState().text).toBe("name: z\n");
  });
});
