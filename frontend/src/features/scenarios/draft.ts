// Черновик сценария: текст YAML — единственный источник правды. Конструктор правит документ
// YAML (комментарии и порядок ключей сохраняются), редактор кода — текст; оба видят одно и то же.
import { YAMLSeq, isMap, isNode, isSeq, parseDocument, visit, type Document } from "yaml";
import { create } from "zustand";

export type Path = (string | number)[];

export interface StepDraft {
  id?: string;
  type?: string;
  enabled?: boolean;
  [param: string]: unknown;
}
export interface InputDraft {
  id?: string;
  source?: string;
  main?: boolean;
  pipeline?: StepDraft[];
}
export interface DatasetDraft {
  id?: string;
  label?: string;
  type?: "table" | "sql" | "python";
  input?: string;
  query?: string;
  code?: string;
  [field: string]: unknown;
}
export interface MetricDraft {
  id?: string;
  label?: string;
  [field: string]: unknown;
}
export interface BlockDraft {
  type?: string;
  id?: string;
  slot?: string;
  shape?: number | { id: number; label?: string };
  [param: string]: unknown;
}
export interface SlideDraft {
  id?: string;
  layout?: string;
  example?: number | { id: number; label?: string };
  enabled?: boolean;
  markers?: Record<string, unknown>;
  blocks?: BlockDraft[];
  keep?: number[];
}
export interface ScenarioDraft {
  spec_version?: number;
  name?: string;
  theme?: string;
  output_name?: string;
  settings?: Record<string, unknown>;
  inputs?: InputDraft[];
  datasets?: DatasetDraft[];
  metrics?: MetricDraft[];
  markers?: Record<string, unknown>;
  slides?: SlideDraft[];
}

/** Как печатать документ: без переноса длинных строк, списки с отступом, как в примерах. */
const PRINT = { lineWidth: 0, indentSeq: true } as const;

export interface ParsedDraft {
  doc: Document.Parsed;
  spec: ScenarioDraft | null;
  error: string | null;
}

export function parseDraft(text: string): ParsedDraft {
  const doc = parseDocument(text, { keepSourceTokens: false });
  if (doc.errors.length) {
    const e = doc.errors[0];
    const line = e.linePos?.[0]?.line;
    return { doc, spec: null, error: `${line ? `строка ${line}: ` : ""}${e.message.split("\n")[0]}` };
  }
  const js = doc.toJS({ maxAliasCount: 100 }) as unknown;
  if (js !== null && (typeof js !== "object" || Array.isArray(js))) return { doc, spec: null, error: "Сценарий — это словарь YAML (name, inputs, …)" };
  return { doc, spec: (js ?? {}) as ScenarioDraft, error: null };
}

function empty(v: unknown): boolean {
  return v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0);
}

/** Поменять значение по пути; пустое значение удаляет ключ (сценарий остаётся коротким). */
export function setIn(doc: Document.Parsed, path: Path, value: unknown): void {
  if (empty(value)) {
    if (doc.hasIn(path)) doc.deleteIn(path);
    return;
  }
  doc.setIn(path, doc.createNode(value));
}

export function addIn(doc: Document.Parsed, path: Path, value: unknown): void {
  const node = doc.getIn(path, true);
  if (isSeq(node)) {
    if (node.flow && node.items.length === 0) node.flow = false; // «datasets: []» → обычный список
    node.add(doc.createNode(value));
  } else doc.setIn(path, doc.createNode([value]));
}

export function moveIn(doc: Document.Parsed, path: Path, from: number, to: number): void {
  const node = doc.getIn(path, true);
  if (!isSeq(node) || to < 0 || to >= node.items.length) return;
  const [item] = node.items.splice(from, 1);
  node.items.splice(to, 0, item);
}

/** Копия элемента списка сразу после него: со свободным id и не основная. Возвращает её номер. */
export function duplicateIn(doc: Document.Parsed, path: Path, taken: (string | undefined)[]): number {
  const list = doc.getIn(path.slice(0, -1), true);
  const i = path[path.length - 1] as number;
  const item = isSeq(list) ? list.items[i] : undefined;
  if (!isSeq(list) || !isNode(item)) return i;
  const copy = item.clone();
  if (isMap(copy)) {
    const id = copy.get("id");
    if (typeof id === "string") copy.set("id", freeId(id, taken));
    copy.delete("main");
  }
  list.items.splice(i + 1, 0, copy);
  return i + 1;
}

export function renameKey(doc: Document.Parsed, path: Path, from: string, to: string): void {
  const node = doc.getIn(path, true);
  if (!isMap(node) || from === to || !to) return;
  const pair = node.items.find((p) => (p.key as { value?: unknown })?.value === from || p.key === from);
  if (pair) pair.key = doc.createNode(to);
}

export function printDoc(doc: Document.Parsed): string {
  // списки в строку без пробелов у скобок, как в примерах и у Prettier: [a, b], но { a: 1 }
  visit(doc, {
    Seq(_, node) {
      if (node.flow && !Object.hasOwn(node, "toString")) {
        node.toString = (ctx, ...rest) => YAMLSeq.prototype.toString.call(node, ctx && { ...ctx, flowCollectionPadding: "" } as never, ...rest);
      }
    },
  });
  return doc.toString(PRINT);
}

/** Пары индексов строк a и b по наибольшей общей подпоследовательности (сравнение по key). */
function matchLines(a: string[], b: string[], key: (s: string) => string = (s) => s): [number, number][] | null {
  const ka = a.map(key);
  const kb = b.map(key);
  let head = 0;
  while (head < ka.length && head < kb.length && ka[head] === kb[head]) head++;
  let tail = 0;
  while (tail < ka.length - head && tail < kb.length - head && ka[ka.length - 1 - tail] === kb[kb.length - 1 - tail]) tail++;
  const n = ka.length - head - tail;
  const m = kb.length - head - tail;
  if (n * m > 9_000_000) return null;
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--) dp[i][j] = ka[head + i] === kb[head + j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: [number, number][] = [];
  for (let k = 0; k < head; k++) out.push([k, k]);
  for (let i = 0, j = 0; i < n && j < m; ) {
    if (ka[head + i] === kb[head + j]) out.push([head + i++, head + j++]);
    else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
    else j++;
  }
  for (let k = tail; k > 0; k--) out.push([ka.length - k, kb.length - k]);
  return out;
}

const squash = (s: string) => s.replace(/\s+/g, "");

/** Правка документа с наименьшим изменением текста.
 *
 * Печать документа (yaml) выравнивает пробелы в скобках и перед комментариями и склеивает
 * перенесённые строки, поэтому текст после правки не печатается целиком. Исходный текст
 * сопоставляется с печатью документа до правки (по строкам без пробелов), печать до правки —
 * с печатью после; строки, которых правка не коснулась, остаются как написал человек. */
export function applyEdit(text: string, doc: Document.Parsed, fn: (doc: Document.Parsed) => void): string {
  const O = text.split("\n");
  const B = printDoc(doc).split("\n");
  const next = doc.clone() as Document.Parsed;
  fn(next);
  const printed = printDoc(next);
  const A = printed.split("\n");
  const ob = matchLines(O, B, squash);
  const ba = matchLines(B, A);
  if (!ob || !ba) return printed;
  const bToA = new Int32Array(B.length).fill(-1);
  for (const [b, a] of ba) bToA[b] = a;
  // Куски: строка, найденная в исходном тексте, или промежуток между такими строками.
  const chunks: { b0: number; b1: number; o0: number; o1: number }[] = [];
  let pb = -1;
  let po = -1;
  for (const [o, b] of ob) {
    if (b > pb + 1 || o > po + 1) chunks.push({ b0: pb + 1, b1: b, o0: po + 1, o1: o });
    chunks.push({ b0: b, b1: b + 1, o0: o, o1: o + 1 });
    pb = b;
    po = o;
  }
  if (pb + 1 < B.length || po + 1 < O.length) chunks.push({ b0: pb + 1, b1: B.length, o0: po + 1, o1: O.length });

  const out: string[] = [];
  let ja = 0;
  for (const c of chunks) {
    if (c.b1 === c.b0) {
      // строки только в исходном тексте: остаются, если соседи на месте
      const before = c.b0 === 0 || bToA[c.b0 - 1] >= 0;
      const after = c.b1 === B.length || bToA[c.b1] >= 0;
      if (before && after) out.push(...O.slice(c.o0, c.o1));
      continue;
    }
    const a0 = bToA[c.b0];
    let intact = a0 >= 0;
    for (let k = c.b0; intact && k < c.b1; k++) intact = bToA[k] === a0 + (k - c.b0);
    if (intact) {
      while (ja < a0) out.push(A[ja++]);
      out.push(...O.slice(c.o0, c.o1));
      ja = a0 + (c.b1 - c.b0);
    } else {
      let last = -1;
      for (let k = c.b0; k < c.b1; k++) last = Math.max(last, bToA[k]);
      while (ja <= last) out.push(A[ja++]);
    }
  }
  while (ja < A.length) out.push(A[ja++]);
  return out.join("\n");
}

// --- состояние черновика открытого сценария ------------------------------------------------

const STASH = "agen-draft:";

/** Несохранённые правки — в хранилище окна: переживают закрытие окна и сбой. */
function stash(scenarioId: string | null, base: string, text: string): void {
  if (!scenarioId) return;
  try {
    if (text === base) localStorage.removeItem(STASH + scenarioId);
    else localStorage.setItem(STASH + scenarioId, JSON.stringify({ base, text }));
  } catch {
    // хранилище недоступно: правки живут, пока открыто окно
  }
}

/** Правки, начатые от той же сохранённой версии; от другой версии — забываются. */
function unstash(scenarioId: string, base: string): string | null {
  try {
    const raw = localStorage.getItem(STASH + scenarioId);
    if (!raw) return null;
    const saved = JSON.parse(raw) as { base?: unknown; text?: unknown };
    if (saved.base === base && typeof saved.text === "string") return saved.text;
    localStorage.removeItem(STASH + scenarioId);
  } catch {
    // нечитаемая запись — как будто её нет
  }
  return null;
}

interface DraftState {
  scenarioId: string | null;
  /** Текст сохранённой версии, от которой начаты правки. */
  base: string;
  text: string;
  parsed: ParsedDraft;
  selected: Path;
  /**
   * Загрузить сохранённый текст (открыт сценарий, сохранена версия, правки отменены). С restore —
   * вернуть несохранённые правки из хранилища окна, если они начаты от этого текста; true — вернули.
   */
  load: (scenarioId: string, text: string, restore?: boolean) => boolean;
  setText: (text: string) => void;
  /** Правка конструктора: функция меняет документ YAML. */
  edit: (fn: (doc: Document.Parsed) => void) => void;
  select: (path: Path) => void;
}

export const useDraft = create<DraftState>()((set, get) => ({
  scenarioId: null,
  base: "",
  text: "",
  parsed: parseDraft(""),
  selected: [],
  load: (scenarioId, text, restore = false) => {
    const kept = restore ? unstash(scenarioId, text) : null;
    const draft = kept ?? text;
    stash(scenarioId, text, draft);
    set((s) => ({ scenarioId, base: text, text: draft, parsed: parseDraft(draft), selected: s.scenarioId === scenarioId ? s.selected : [] }));
    return kept !== null && kept !== text;
  },
  setText: (text) => {
    set({ text, parsed: parseDraft(text) });
    stash(get().scenarioId, get().base, text);
  },
  edit: (fn) => {
    const { parsed, text } = get();
    if (parsed.error) return;
    const next = applyEdit(text, parsed.doc, fn);
    set({ text: next, parsed: parseDraft(next) });
    stash(get().scenarioId, get().base, next);
  },
  select: (selected) => set({ selected }),
}));

export const isDirty = (s: Pick<DraftState, "base" | "text">) => s.base !== s.text;

// --- узлы черновика ------------------------------------------------------------------------

/** Узел превью для выбранного места сценария (или null: у места нет превью). */
export function previewTarget(spec: ScenarioDraft | null, path: Path): { kind: "node"; target: string } | { kind: "slide"; number: number } | null {
  if (!spec || !path.length) return null;
  const [group, i, sub, j] = path;
  if (typeof i !== "number") return null;
  if (group === "inputs") {
    const input = spec.inputs?.[i];
    if (!input?.id) return null;
    if (sub === "pipeline" && typeof j === "number") {
      const step = input.pipeline?.[j];
      if (!step?.id) return null;
      if (step.enabled === false) return null;
      return { kind: "node", target: `input:${input.id}/step:${step.id}` };
    }
    return { kind: "node", target: `input:${input.id}` };
  }
  if (group === "datasets") {
    const id = spec.datasets?.[i]?.id;
    return id ? { kind: "node", target: `dataset:${id}` } : null;
  }
  if (group === "metrics") {
    const id = spec.metrics?.[i]?.id;
    return id ? { kind: "node", target: `metric:${id}` } : null;
  }
  if (group === "slides") {
    const slides = spec.slides ?? [];
    if (!slides[i] || slides[i].enabled === false) return null;
    const number = slides.slice(0, i + 1).filter((s) => s.enabled !== false).length;
    return { kind: "slide", number };
  }
  return null;
}

/** Место в сценарии для узла замечания: input:sales/step:dedupe, dataset:…, metric:…, slide:3/block:… */
export function issuePath(spec: ScenarioDraft | null, node: string | null | undefined): Path | null {
  if (!spec || !node) return null;
  const [head, tail] = node.split("/", 2);
  const [kind, id] = head.split(":", 2);
  if (kind === "input") {
    const i = (spec.inputs ?? []).findIndex((x) => x.id === id);
    if (i < 0) return null;
    const step = tail?.startsWith("step:") ? tail.slice(5) : null;
    const j = step ? (spec.inputs![i].pipeline ?? []).findIndex((x) => x.id === step) : -1;
    return j >= 0 ? ["inputs", i, "pipeline", j] : ["inputs", i];
  }
  if (kind === "dataset") {
    const i = (spec.datasets ?? []).findIndex((x) => x.id === id);
    return i >= 0 ? ["datasets", i] : null;
  }
  if (kind === "metric") {
    // показатели сравнения (revenue_prev, …) принадлежат своему показателю
    const ms = spec.metrics ?? [];
    let i = ms.findIndex((x) => x.id === id);
    if (i < 0) i = ms.findIndex((x) => !!x.id && id.startsWith(`${x.id}_`));
    return i >= 0 ? ["metrics", i] : null;
  }
  if (kind === "slide") {
    const n = Number(id);
    let seen = 0;
    const slides = spec.slides ?? [];
    for (let i = 0; i < slides.length; i++) {
      if (slides[i].enabled === false) continue;
      if (++seen === n) return ["slides", i];
    }
    return null;
  }
  return null;
}

/** Новый id, которого ещё нет среди данных: base, base_2, base_3, … */
export function freeId(base: string, taken: (string | undefined)[]): string {
  const ids = new Set(taken.filter(Boolean));
  if (!ids.has(base)) return base;
  for (let n = 2; ; n++) if (!ids.has(`${base}_${n}`)) return `${base}_${n}`;
}

/** id для текста: латиница, цифры и «_», как у id в сценарии. */
export function slug(text: string, fallback = "item"): string {
  const map: Record<string, string> = {
    а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o",
    п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "ts", ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya",
  };
  const s = [...text.toLowerCase()].map((c) => map[c] ?? c).join("").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  return (/^[a-z]/.test(s) ? s : s ? `${fallback}_${s}` : fallback).slice(0, 40);
}

/** Номер слайда шаблона из записи example/shape: 256 или {id: 256}. */
export function refId(ref: number | { id: number } | undefined | null): number | null {
  if (ref == null) return null;
  return typeof ref === "number" ? ref : ref.id;
}
