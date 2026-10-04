// Переименование id входа, набора или показателя вместе со ссылками на него в сценарии.
import type { Document } from "yaml";
import { setIn, type ScenarioDraft } from "../draft";

/** Ссылки в полях сценария (не в тексте формул, запросов и кода — их человек правит сам). */
export function renameRefs(doc: Document.Parsed, spec: ScenarioDraft, kind: "input" | "dataset" | "metric", from: string, to: string): void {
  const swapList = (path: (string | number)[], list: unknown) => {
    if (Array.isArray(list) && list.includes(from)) setIn(doc, path, list.map((x) => (x === from ? to : x)));
  };
  (spec.datasets ?? []).forEach((d, i) => {
    if (kind === "input" && d.input === from) setIn(doc, ["datasets", i, "input"], to);
    if (kind !== "metric") swapList(["datasets", i, "inputs"], d.inputs);
  });
  (spec.metrics ?? []).forEach((m, i) => {
    if (kind === "input" && m.input === from) setIn(doc, ["metrics", i, "input"], to);
    if (kind === "dataset" && m.dataset === from) setIn(doc, ["metrics", i, "dataset"], to);
    if (kind !== "metric") swapList(["metrics", i, "inputs"], m.inputs);
  });
  if (kind === "input") {
    (spec.inputs ?? []).forEach((inp, i) =>
      (inp.pipeline ?? []).forEach((st, j) => {
        if (st.with === from) setIn(doc, ["inputs", i, "pipeline", j, "with"], to);
      }),
    );
  }
  if (kind === "dataset") {
    (spec.slides ?? []).forEach((sl, i) =>
      (sl.blocks ?? []).forEach((b, j) => {
        if (b.dataset === from) setIn(doc, ["slides", i, "blocks", j, "dataset"], to);
      }),
    );
  }
  if (kind === "metric") {
    const swapMarkers = (path: (string | number)[], markers: Record<string, unknown> | undefined) =>
      Object.entries(markers ?? {}).forEach(([name, b]) => {
        if (b === from) setIn(doc, [...path, name], to);
        else if (b && typeof b === "object" && (b as { metric?: string }).metric === from) setIn(doc, [...path, name, "metric"], to);
      });
    swapMarkers(["markers"], spec.markers);
    (spec.slides ?? []).forEach((sl, i) => swapMarkers(["slides", i, "markers"], sl.markers));
  }
}
