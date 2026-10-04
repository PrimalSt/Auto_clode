// Что нужно формам конструктора: черновик, источники, шаблон, модули и подсказки столбцов.
import { create } from "zustand";
import type { Schemas, SourceRecord, ThemeManifest } from "../../../shared/api/client";
import { usePlugins, type PluginInfo } from "../../../shared/api/modules";
import { useScenarioSchema } from "../../../shared/api/schemas";
import { useSources } from "../../sources/queries";
import { useTheme } from "../../themes/queries";
import type { DatasetDraft, InputDraft, ScenarioDraft } from "../draft";
import type { Hints, JsonSchema } from "./SchemaForm";

/** Столбцы, которые превью уже показало для узла (input:…, input:…/step:…, dataset:…). */
interface PreviewColumns {
  columns: Record<string, string[]>;
  put: (target: string, columns: string[]) => void;
}

export const usePreviewColumns = create<PreviewColumns>()((set) => ({
  columns: {},
  put: (target, columns) => set((s) => ({ columns: { ...s.columns, [target]: columns } })),
}));

/** Число строк до и после каждого шага входа из последнего превью (вход → шаги). */
interface PreviewSteps {
  steps: Record<string, Schemas["StepStat"][]>;
  put: (input: string, steps: Schemas["StepStat"][]) => void;
}

export const usePreviewSteps = create<PreviewSteps>()((set) => ({
  steps: {},
  put: (input, steps) => set((s) => ({ steps: { ...s.steps, [input]: steps } })),
}));

const uniq = (xs: (string | undefined | null)[]) => [...new Set(xs.filter((x): x is string => !!x))];

/** Столбцы входа: столбцы источника, переименования и новые столбцы шагов, столбцы из превью. */
export function inputColumns(input: InputDraft | undefined, sources: SourceRecord[], seen: Record<string, string[]>): string[] {
  if (!input) return [];
  const src = sources.find((s) => s.id === input.source);
  const cols: (string | undefined)[] = [...(src?.spec.columns.map((c) => c.id) ?? [])];
  for (const st of input.pipeline ?? []) {
    if (typeof st.column === "string" && (st.type === "formula" || st.type === "python")) cols.push(st.column);
    if (st.type === "rename" && st.columns && typeof st.columns === "object") cols.push(...Object.values(st.columns as Record<string, string>));
    if (Array.isArray(st.adds)) cols.push(...(st.adds as string[]));
  }
  const last = input.pipeline?.length ? `input:${input.id}/step:${input.pipeline[input.pipeline.length - 1].id}` : null;
  return uniq([...cols, ...(seen[`input:${input.id}`] ?? []), ...(last ? (seen[last] ?? []) : [])]);
}

function aggName(a: Record<string, unknown>): string | undefined {
  if (typeof a.as === "string") return a.as;
  if (typeof a.fn !== "string") return undefined;
  return typeof a.column === "string" ? `${a.fn}_${a.column}` : a.fn;
}

/** Столбцы набора: группировка, агрегаты, сравнения и расчёты; без агрегатов — столбцы входа. */
export function datasetColumns(ds: DatasetDraft | undefined, spec: ScenarioDraft, sources: SourceRecord[], seen: Record<string, string[]>): string[] {
  if (!ds) return [];
  const cols: (string | undefined)[] = [];
  const groups = (Array.isArray(ds.group_by) ? ds.group_by : []) as unknown[];
  for (const g of groups) cols.push(typeof g === "string" ? g : ((g as { column?: string })?.column ?? undefined));
  const aggs = (Array.isArray(ds.aggregate) ? ds.aggregate : []) as Record<string, unknown>[];
  const names = aggs.map(aggName);
  cols.push(...names);
  for (const c of (Array.isArray(ds.compare) ? ds.compare : []) as unknown[]) {
    const w = typeof c === "string" ? c : (c as { window?: string; suffix?: string })?.window;
    const suffix = (typeof c === "object" && c && (c as { suffix?: string }).suffix) || (w === "same_period_last_year" ? "ly" : "prev");
    for (const n of names) if (n) cols.push(`${n}_${suffix}`, `${n}_${suffix}_change`, `${n}_${suffix}_change_pct`);
  }
  for (const d of (Array.isArray(ds.derive) ? ds.derive : []) as Record<string, unknown>[]) {
    cols.push(typeof d.as === "string" ? d.as : typeof d.column === "string" && typeof d.fn === "string" ? `${d.column}_${d.fn}` : undefined);
  }
  if (!aggs.length && (ds.type ?? "table") === "table") {
    const own = Array.isArray(ds.columns) ? (ds.columns as string[]) : [];
    cols.push(...(own.length ? own : inputColumns(spec.inputs?.find((i) => i.id === ds.input), sources, seen)));
  }
  return uniq([...cols, ...(seen[`dataset:${ds.id}`] ?? [])]);
}

export interface ConstructorData {
  sources: SourceRecord[];
  manifest: ThemeManifest | null;
  /** Шаблон сценария не из папки данных (путь к файлу) или не найден. */
  themeMissing: boolean;
  schema: JsonSchema | undefined;
  steps: PluginInfo[];
  windows: PluginInfo[];
  blocks: PluginInfo[];
  aggregations: PluginInfo[];
  seen: Record<string, string[]>;
  /** Подсказки для форм; columns — по месту в сценарии (см. hintsFor*). */
  base: Hints;
}

export function useConstructorData(spec: ScenarioDraft | null): ConstructorData {
  const sources = useSources().data ?? [];
  const themeId = typeof spec?.theme === "string" && /^[\w-]+$/.test(spec.theme) ? spec.theme : "";
  const theme = useTheme(themeId);
  const schema = useScenarioSchema().data;
  const steps = usePlugins("step");
  const windows = usePlugins("window");
  const blocks = usePlugins("block");
  const aggregations = usePlugins("aggregation");
  const seen = usePreviewColumns((s) => s.columns);
  const metricIds = (spec?.metrics ?? []).flatMap((m) => {
    if (!m.id) return [];
    const cmp = (Array.isArray(m.compare) ? m.compare : []) as unknown[];
    const extra = cmp.flatMap((c) => {
      const w = typeof c === "string" ? c : (c as { window?: string })?.window;
      const suffix = (typeof c === "object" && c && (c as { suffix?: string }).suffix) || (w === "same_period_last_year" ? "ly" : "prev");
      return [`${m.id}_${suffix}`, `${m.id}_${suffix}_change`, `${m.id}_${suffix}_change_pct`];
    });
    return [m.id, ...extra];
  });
  return {
    sources,
    manifest: theme.data?.current.manifest ?? null,
    themeMissing: !!spec?.theme && (!themeId || !!theme.error),
    schema,
    steps,
    windows,
    blocks,
    aggregations,
    seen,
    base: {
      inputs: uniq((spec?.inputs ?? []).map((i) => i.id)),
      datasets: uniq((spec?.datasets ?? []).map((d) => d.id)),
      metrics: uniq(metricIds),
      aggregations: aggregations.map((a) => ({ value: a.name, label: `${a.title} (${a.name})` })),
    },
  };
}

/** Схема узла сценария из общей схемы (DatasetSpec, MetricSpec, …) с её $defs. */
export function nodeSchema(schema: JsonSchema | undefined, name: string): { schema: JsonSchema; defs: Record<string, JsonSchema> } | null {
  const def = schema?.$defs?.[name];
  if (!def) return null;
  return { schema: def, defs: schema.$defs ?? {} };
}

export function pluginTitle(plugins: PluginInfo[], type: string | undefined): string {
  return plugins.find((p) => p.name === type)?.title ?? type ?? "?";
}
