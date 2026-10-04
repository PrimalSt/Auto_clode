// Привязка метки {{…}} слайда-образца: показатель, переменная периода, текст или готовое значение.
import { Badge, Button, Collapse, Group, Select, Stack, Switch, Text, TextInput, Textarea } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { SchemaForm, type JsonSchema } from "./SchemaForm";

type Mode = "metric" | "period" | "text" | "value" | "none";

const MODES = [
  { value: "metric", label: "показатель" },
  { value: "period", label: "период" },
  { value: "text", label: "текст" },
  { value: "value", label: "значение" },
];

const OWN = ["metric", "period", "text", "value", "capital"];

/** Привязка в виде словаря: короткие записи развёрнуты. */
export function readBinding(value: unknown): Record<string, unknown> {
  if (typeof value === "number") return { value: String(value) };
  if (typeof value === "string") {
    if (!value.startsWith("period.")) return { metric: value };
    const name = value.slice(7);
    return name[0] && name[0] === name[0].toUpperCase() && name[0] !== name[0].toLowerCase()
      ? { period: name[0].toLowerCase() + name.slice(1), capital: true }
      : { period: name };
  }
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

/** Привязка покороче: только показатель — его id, только период — period.month (Month). */
export function writeBinding(b: Record<string, unknown>): unknown {
  const keys = Object.keys(b);
  if (!keys.length) return undefined;
  if (keys.length === 1 && typeof b.metric === "string") return b.metric;
  if (typeof b.period === "string" && keys.every((k) => k === "period" || k === "capital")) {
    const p = b.capital ? b.period[0].toUpperCase() + b.period.slice(1) : b.period;
    return `period.${p}`;
  }
  return b;
}

function modeOf(b: Record<string, unknown>): Mode {
  for (const m of ["metric", "period", "text", "value"] as const) if (b[m] !== undefined && b[m] !== null) return m;
  return "none";
}

/** Переменные периода — из описания поля period в схеме привязки. */
export function periodVars(schema: JsonSchema | undefined, defs: Record<string, JsonSchema>): string[] {
  const def = schema?.$ref ? defs[schema.$ref.replace("#/$defs/", "")] : schema;
  const desc = def?.properties?.period?.description ?? "";
  const list = desc.split(":")[1];
  return list ? list.split(",").map((s) => s.trim()).filter(Boolean) : [];
}

interface Props {
  name: string;
  value: unknown;
  onChange: (value: unknown) => void;
  metrics: string[];
  /** Схема MarkerBinding (из параметров модуля markers) и её $defs. */
  schema: JsonSchema | undefined;
  defs: Record<string, JsonSchema>;
  /** Привязка из общих привязок сценария: действует, пока на слайде своей нет. */
  inherited?: unknown;
  /** Почему метку нельзя заменить (метка разбита на части с разным оформлением и т. п.). */
  problem?: string | null;
}

export function MarkerField({ name, value, onChange, metrics, schema, defs, inherited, problem }: Props) {
  const [open, toggle] = useDisclosure(false);
  const b = readBinding(value);
  const mode = modeOf(b);
  const vars = periodVars(schema, defs);
  const set = (next: Record<string, unknown>) => onChange(writeBinding(next));
  const setMode = (m: string | null) => {
    const rest = Object.fromEntries(Object.entries(b).filter(([k]) => !OWN.includes(k)));
    if (!m || m === "none") return onChange(undefined);
    set({ ...rest, [m]: m === "period" ? (vars[0] ?? "month") : m === "metric" ? (metrics[0] ?? "") : "" });
  };
  const own = Object.fromEntries(Object.entries(b).filter(([k]) => !OWN.includes(k)));
  return (
    <Stack gap={4}>
      <Text ff="monospace" size="sm" fw={500}>
        {`{{${name}}}`}
      </Text>
      <Group gap="xs" align="flex-end">
        <Select size="xs" w={140} data={MODES} value={mode === "none" ? null : mode} placeholder="не привязана" onChange={setMode} clearable aria-label="Что подставить" />
        {mode === "metric" && (
          <Select
            size="xs"
            miw={180}
            style={{ flex: 1 }}
            data={[...new Set([...metrics, String(b.metric ?? "")])].filter(Boolean)}
            value={String(b.metric ?? "")}
            searchable
            onChange={(x) => x && set({ ...b, metric: x })}
            aria-label="Показатель"
          />
        )}
        {mode === "period" && (
          <Select
            size="xs"
            miw={180}
            style={{ flex: 1 }}
            data={[...new Set([...vars, String(b.period ?? "")])].filter(Boolean)}
            value={String(b.period ?? "")}
            searchable
            onChange={(x) => x && set({ ...b, period: x })}
            aria-label="Переменная периода"
          />
        )}
        {mode === "value" && <TextInput size="xs" miw={180} style={{ flex: 1 }} value={String(b.value ?? "")} onChange={(e) => set({ ...b, value: e.currentTarget.value })} aria-label="Значение" />}
        {mode === "period" && <Switch size="xs" label="С заглавной" checked={!!b.capital} onChange={(e) => set({ ...b, capital: e.currentTarget.checked || undefined })} />}
        {mode !== "none" && (
          <Button size="compact-xs" variant="subtle" onClick={toggle.toggle}>
            {open ? "Скрыть формат" : "Формат"}
          </Button>
        )}
        {mode === "none" && inherited === undefined && !problem && (
          <Badge size="xs" color="yellow" variant="light">
            не привязана
          </Badge>
        )}
      </Group>
      {mode === "text" && (
        <Textarea
          size="xs"
          autosize
          minRows={1}
          placeholder="Текст с переменными: {{ metrics.revenue | money }}, {{ period.label }}"
          value={String(b.text ?? "")}
          onChange={(e) => set({ ...b, text: e.currentTarget.value })}
        />
      )}
      {mode === "none" && inherited !== undefined && (
        <Text size="xs" c="dimmed">
          Из общих привязок сценария: <code>{typeof inherited === "string" ? inherited : JSON.stringify(inherited)}</code>
        </Text>
      )}
      {problem && (
        <Text size="xs" c="orange">
          {problem}
        </Text>
      )}
      {schema && (
        <Collapse expanded={open && mode !== "none"}>
          <div>
            <SchemaForm
              schema={schema}
              defs={defs}
              value={own}
              omit={OWN}
              always={["decimals", "scale", "percent", "sign"]}
              onField={(k, x) => {
                const next = { ...b, [k]: x };
                if (x === undefined) delete next[k];
                set(next);
              }}
            />
          </div>
        </Collapse>
      )}
    </Stack>
  );
}
