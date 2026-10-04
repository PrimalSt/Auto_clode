// Monaco и его воркеры собираются вместе с окном: приложение работает без интернета.
import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import { configureMonacoYaml } from "monaco-yaml";
import YamlWorker from "monaco-yaml/yaml.worker?worker";

function createWorker(label: string): Worker {
  return label === "yaml" ? new YamlWorker() : new EditorWorker();
}

self.MonacoEnvironment = { getWorker: (_id: string, label: string) => createWorker(label) };
loader.config({ monaco });

interface LegacyWorkerOptions {
  label?: string;
  createData?: unknown;
  host?: Record<string, (...args: unknown[]) => unknown>;
  keepIdleModels?: boolean;
}

/**
 * monaco-yaml создаёт воркер по-старому (label, createData), а Monaco 0.57 ждёт готовый Worker;
 * без перевода проверка YAML молча уходит в главный поток и не работает. Первое сообщение
 * будит воркер, второе передаёт ему createData — так же Monaco делает это для своих языков.
 */
function createWebWorker(opts: LegacyWorkerOptions | Parameters<typeof monaco.editor.createWebWorker>[0]) {
  if ("worker" in opts) return monaco.editor.createWebWorker(opts);
  const worker = createWorker(opts.label ?? "");
  worker.postMessage("ignore");
  worker.postMessage(opts.createData);
  return monaco.editor.createWebWorker({ worker, host: opts.host, keepIdleModels: opts.keepIdleModels });
}

export const SCENARIO_MODEL_PREFIX = "file:///scenario-";

/** Общие настройки редакторов; кириллица не подсвечивается как «похожая на латиницу». */
export const EDITOR_OPTIONS = {
  minimap: { enabled: false },
  fontSize: 13,
  scrollBeyondLastLine: false,
  automaticLayout: true,
  unicodeHighlight: { ambiguousCharacters: false },
} satisfies monaco.editor.IEditorOptions;

const yaml = configureMonacoYaml({ ...monaco, editor: { ...monaco.editor, createWebWorker } } as typeof monaco, {
  enableSchemaRequest: false,
  schemas: [],
});
let current: unknown = null;

/** Схема сценария для проверки и подсказок YAML (с сервера: короткие записи и параметры модулей). */
export function setScenarioSchema(schema: unknown): void {
  if (schema === current) return;
  current = schema;
  void yaml.update({
    enableSchemaRequest: false,
    schemas: [{ uri: "agen://schemas/scenario.json", fileMatch: [`${SCENARIO_MODEL_PREFIX}*`], schema: schema as never }],
  });
}

export { monaco };
