// Окно данных: отчётный период, с начала квартала, последние N периодов, диапазон, …
import { Group, Select, Stack } from "@mantine/core";
import type { PluginInfo } from "../../../shared/api/modules";
import { SchemaForm, type JsonSchema } from "./SchemaForm";

const DEFAULT = "report_period";

/** Окно из записи сценария: "quarter_to_date", "last_n(6)", {type: range, start, end}. */
export function parseWindow(value: unknown): { type: string; params: Record<string, unknown> } {
  if (typeof value === "string") {
    const m = /^\s*(\w+)\s*(?:\((.*)\))?\s*$/.exec(value);
    if (!m) return { type: value, params: {} };
    const args = (m[2] ?? "").split(",").map((a) => a.trim()).filter(Boolean);
    if (m[1] === "last_n" && args.length === 1) return { type: "last_n", params: { n: Number(args[0]) } };
    if (m[1] === "range" && args.length === 2) return { type: "range", params: { start: args[0], end: args[1] } };
    return { type: m[1], params: {} };
  }
  if (value && typeof value === "object") {
    const { type, ...params } = value as Record<string, unknown>;
    return { type: typeof type === "string" ? type : DEFAULT, params };
  }
  return { type: DEFAULT, params: {} };
}

/** Запись окна покороче: без параметров — имя, last_n — last_n(6), остальное — словарём. */
export function writeWindow(type: string, params: Record<string, unknown>): unknown {
  const keys = Object.keys(params);
  if (!keys.length) return type === DEFAULT ? undefined : type;
  if (type === "last_n" && keys.length === 1 && typeof params.n === "number") return `last_n(${params.n})`;
  return { type, ...params };
}

export function WindowField({ value, onChange, windows, label = "Окно данных" }: { value: unknown; onChange: (v: unknown) => void; windows: PluginInfo[]; label?: string }) {
  const { type, params } = parseWindow(value);
  const plugin = windows.find((w) => w.name === type);
  const schema = plugin?.params_schema as JsonSchema | undefined;
  const hasParams = !!schema && Object.keys(schema.properties ?? {}).length > 0;
  const data = windows.map((w) => ({ value: w.name, label: w.title }));
  if (!plugin && type) data.push({ value: type, label: type });
  return (
    <Stack gap={6}>
      <Select
        label={label}
        description="Какой отрезок истории берётся относительно отчётного периода"
        data={data}
        value={type}
        allowDeselect={false}
        onChange={(t) => t && onChange(writeWindow(t, {}))}
      />
      {hasParams && (
        <Group grow align="flex-start">
          <SchemaForm
            schema={schema}
            value={params}
            onField={(k, x) => {
              const next = { ...params, [k]: x };
              if (x === undefined) delete next[k];
              onChange(writeWindow(type, next));
            }}
          />
        </Group>
      )}
    </Stack>
  );
}
