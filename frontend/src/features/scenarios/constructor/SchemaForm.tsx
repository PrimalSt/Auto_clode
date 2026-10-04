// Форма по JSON Schema параметров модуля (шаг, окно, блок) или узла сценария. Поля, которые
// не обязательны и не заданы, спрятаны под «Все параметры»: форма короткая, как сценарий.
import {
  ActionIcon,
  Autocomplete,
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  Stack,
  Switch,
  TagsInput,
  Text,
  TextInput,
  Textarea,
} from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { useState, type ReactNode } from "react";
import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import { CodeEditor } from "../../../shared/components/CodeEditor";
import { FIELD_LABELS } from "../../../shared/labels";

export interface JsonSchema {
  type?: string | string[];
  enum?: unknown[];
  const?: unknown;
  anyOf?: JsonSchema[];
  allOf?: JsonSchema[];
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  additionalProperties?: boolean | JsonSchema;
  default?: unknown;
  description?: string;
  title?: string;
  format?: string;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  minItems?: number;
  /** Короткая запись узла (contracts.short_form): в манифесте модулей она не развёрнута в anyOf. */
  "x-short"?: JsonSchema;
}

/** Подсказки для полей: что можно выбрать в этом месте сценария. */
export interface Hints {
  columns?: string[];
  inputs?: string[];
  datasets?: string[];
  metrics?: string[];
  slots?: string[];
  aggregations?: { value: string; label: string }[];
}

type HintKind = Exclude<keyof Hints, "aggregations">;

// Поле → подсказка; «Модель.поле» уточняет, если одно имя значит разное.
const HINT_BY_FIELD: Record<string, HintKind | null> = {
  column: "columns",
  columns: "columns",
  by: "columns",
  drop: "columns",
  x: "columns",
  categories: "columns",
  fill: "columns",
  pivot: "columns",
  "SeriesFrom.value": "columns",
  "Condition.value": null,
  value: null,
  with: "inputs",
  input: "inputs",
  dataset: "datasets",
  metric: "metrics",
  slot: "slots",
};

const MULTILINE = new Set(["code", "query", "text"]);
const MONO = new Set(["code", "query", "expr", "where", "formula", "number_format", "pattern"]);

interface Ctx {
  defs: Record<string, JsonSchema>;
  hints: Hints;
}

export function resolve(schema: JsonSchema | undefined, defs: Record<string, JsonSchema>): JsonSchema {
  let s = schema ?? {};
  for (let i = 0; s.$ref && i < 10; i++) s = { ...defs[s.$ref.replace("#/$defs/", "")], ...omitKey(s, "$ref") };
  return s;
}

function omitKey<T extends object>(o: T, key: string): T {
  const { [key]: _, ...rest } = o as Record<string, unknown>;
  return rest as T;
}

/** Варианты anyOf без null и признак «можно не задавать». */
function variants(s: JsonSchema, defs: Record<string, JsonSchema>): { list: JsonSchema[]; nullable: boolean } {
  if (!s.anyOf) return { list: [s], nullable: false };
  const list = s.anyOf.map((v) => resolve(v, defs)).filter((v) => v.type !== "null");
  return { list, nullable: list.length < s.anyOf.length };
}

function typeOf(s: JsonSchema): string | undefined {
  if (s.enum || s.const !== undefined) return "enum";
  if (Array.isArray(s.type)) return s.type.find((t) => t !== "null");
  if (s.type) return s.type;
  if (s.properties) return "object";
  return undefined;
}

function matches(value: unknown, s: JsonSchema): boolean {
  const t = typeOf(s);
  if (value === undefined || value === null) return false;
  if (t === "enum") return (s.enum ?? [s.const]).includes(value);
  if (t === "array") return Array.isArray(value);
  if (t === "object") return typeof value === "object" && !Array.isArray(value);
  if (t === "integer" || t === "number") return typeof value === "number";
  if (t === "boolean") return typeof value === "boolean";
  if (t === "string") return typeof value === "string";
  return false;
}

/** Короткая запись объекта (строка вместо {column: …}): поле, которое она задаёт. */
function shortKey(obj: JsonSchema): string | null {
  const req = obj.required ?? [];
  return req.length === 1 ? req[0] : null;
}

/** Короткая запись ↔ объект. По умолчанию строка задаёт единственное обязательное поле;
 * у сортировки «-столбец» значит «по убыванию». */
function readShort(schema: JsonSchema, key: string, value: unknown): Record<string, unknown> {
  if (typeof value !== "string" && typeof value !== "number") return (value ?? {}) as Record<string, unknown>;
  if (schema.title === "SortSpec" && typeof value === "string") {
    return value.startsWith("-") ? { column: value.slice(1), desc: true } : { column: value };
  }
  return { [key]: value };
}

function writeShort(schema: JsonSchema, key: string, obj: Record<string, unknown>): unknown {
  const keys = Object.keys(obj);
  if (schema.title === "SortSpec" && typeof obj.column === "string" && keys.every((k) => k === "column" || k === "desc")) {
    return obj.desc ? `-${obj.column}` : obj.column;
  }
  return keys.length === 1 && keys[0] === key && typeof obj[key] === "string" ? obj[key] : obj;
}

/** Схема поля после разбора anyOf: объект с короткой записью показывается формой объекта. */
function pick(s: JsonSchema, value: unknown, ctx: Ctx): { schema: JsonSchema; nullable: boolean; short: string | null; choices: JsonSchema[] } {
  const { list, nullable } = variants(s, ctx.defs);
  if (list.length === 1 && list[0]["x-short"] && typeOf(list[0]) === "object" && shortKey(list[0])) {
    return { schema: { ...list[0], description: list[0].description ?? s.description }, nullable, short: shortKey(list[0]), choices: [] };
  }
  if (list.length === 1) return { schema: { ...list[0], description: list[0].description ?? s.description, default: s.default ?? list[0].default }, nullable, short: null, choices: [] };
  const obj = list.find((v) => typeOf(v) === "object");
  const str = list.find((v) => typeOf(v) === "string" || typeOf(v) === "enum" || (Array.isArray(v.type) && v.type.includes("string")));
  if (obj && str && list.length === 2 && shortKey(obj)) {
    return { schema: { ...obj, description: obj.description ?? s.description }, nullable, short: shortKey(obj), choices: [] };
  }
  const current = list.find((v) => matches(value, v)) ?? list[0];
  return { schema: { ...current, description: current.description ?? s.description, default: s.default }, nullable, short: null, choices: list };
}

export function fieldLabel(name: string, s: JsonSchema, owner?: string): { label: string; description?: string } {
  const own = (owner && FIELD_LABELS[`${owner}.${name}`]) || FIELD_LABELS[name];
  const desc = s.description?.split("\n")[0];
  if (own) return { label: own, description: desc };
  if (desc) return { label: desc.split(/[;:(]/)[0].trim() };
  return { label: s.title ?? name };
}

function asText(v: unknown): string {
  if (v === undefined || v === null) return "";
  if (typeof v === "string") return v;
  return stringifyYaml(v, { flow: true }).trim();
}

function hintFor(name: string, owner: string | undefined, hints: Hints): string[] | undefined {
  const key = owner && `${owner}.${name}` in HINT_BY_FIELD ? `${owner}.${name}` : name;
  const kind = HINT_BY_FIELD[key];
  return kind ? hints[kind] : undefined;
}

function placeholder(s: JsonSchema): string | undefined {
  if (s.default === undefined || s.default === null) return undefined;
  return `по умолчанию: ${asText(s.default)}`;
}

interface FieldProps {
  name: string;
  owner?: string;
  schema: JsonSchema;
  value: unknown;
  required?: boolean;
  onChange: (value: unknown) => void;
  ctx: Ctx;
  bare?: boolean;
}

/** Одно поле формы: вид выбирается по схеме. */
export function Field({ name, owner, schema, value, required, onChange, ctx, bare }: FieldProps): ReactNode {
  const p = pick(resolve(schema, ctx.defs), value, ctx);
  const s = p.schema;
  const { label, description } = bare ? { label: undefined, description: undefined } : fieldLabel(name, s, owner);
  const common = { label, description, withAsterisk: required && !bare, placeholder: placeholder(s) };
  const t = typeOf(s);
  const v = p.short ? readShort(s, p.short, value) : value;

  if (p.choices.length > 1 && !(t === "enum" || t === "string" || t === "integer" || t === "number")) {
    // Несколько видов значения (например, ключи списком или словарём): текстом YAML.
    return <YamlField {...common} value={value} onChange={onChange} />;
  }
  if (t === "enum") {
    const opts = (s.enum ?? [s.const]).map((o) => String(o));
    const extra = p.choices.length > 1 && typeof value === "string" && !opts.includes(value) ? [value] : [];
    return (
      <Select
        {...common}
        data={[...opts, ...extra]}
        value={value === undefined || value === null ? null : String(value)}
        onChange={(x) => onChange(x ?? undefined)}
        clearable={!required}
        searchable={opts.length > 8}
      />
    );
  }
  if (t === "boolean") {
    const checked = typeof value === "boolean" ? value : Boolean(s.default);
    return (
      <Switch
        label={label}
        description={description}
        checked={checked}
        onChange={(e) => onChange(e.currentTarget.checked === Boolean(s.default) && !required ? undefined : e.currentTarget.checked)}
      />
    );
  }
  if (t === "integer" || t === "number") {
    return (
      <NumberInput
        {...common}
        value={typeof value === "number" ? value : ""}
        onChange={(x) => onChange(typeof x === "number" ? x : x === "" ? undefined : Number(x))}
        allowDecimal={t === "number"}
        min={s.minimum ?? (s.exclusiveMinimum !== undefined ? s.exclusiveMinimum : undefined)}
        max={s.maximum}
      />
    );
  }
  if (t === "string") {
    const text = typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
    const set = (x: string) => onChange(x === "" ? undefined : x);
    const font = MONO.has(name) ? { ff: "monospace" } : {};
    if (name === "fn" && ctx.hints.aggregations) {
      return <Select {...common} data={ctx.hints.aggregations} value={text || null} onChange={(x) => set(x ?? "")} searchable clearable={!required} />;
    }
    if (name === "code" || name === "query") {
      return <CodeEditor label={label} description={description} required={required} language={name === "code" ? "python" : "sql"} value={text} onChange={set} />;
    }
    if (MULTILINE.has(name)) {
      return <Textarea {...common} {...font} autosize minRows={name === "text" ? 2 : 4} maxRows={24} value={text} onChange={(e) => set(e.currentTarget.value)} />;
    }
    const data = hintFor(name, owner, ctx.hints);
    if (data?.length) return <Autocomplete {...common} {...font} data={[...new Set(data)]} value={text} onChange={set} />;
    return <TextInput {...common} {...font} value={text} onChange={(e) => set(e.currentTarget.value)} />;
  }
  if (t === "array") {
    const items = resolve(s.items, ctx.defs);
    const ip = pick(items, undefined, ctx);
    const it = typeOf(ip.schema);
    if ((it === "string" || it === "enum" || it === "integer" || it === "number") && !ip.short) {
      const list = Array.isArray(value) ? value.map(String) : [];
      const data = it === "enum" ? (ip.schema.enum ?? []).map(String) : hintFor(name, owner, ctx.hints);
      const num = it === "integer" || it === "number";
      return (
        <TagsInput
          {...common}
          data={data ? [...new Set(data)] : []}
          value={list}
          onChange={(x) => onChange(x.length ? (num ? x.map(Number).filter((n) => !Number.isNaN(n)) : x) : undefined)}
          clearable
        />
      );
    }
    if (it === "object") {
      return <ObjectList name={name} label={label} description={description} schema={ip.schema} short={ip.short} value={value} onChange={onChange} ctx={ctx} />;
    }
    return <YamlField {...common} value={value} onChange={onChange} />;
  }
  if (t === "object") {
    if (s.properties && Object.keys(s.properties).length) {
      const obj = (v && typeof v === "object" && !Array.isArray(v) ? v : {}) as Record<string, unknown>;
      return (
        <Card withBorder padding="sm" radius="sm">
          {label && (
            <Text size="sm" fw={500} mb={4}>
              {label}
            </Text>
          )}
          <SchemaForm
            schema={s}
            defs={ctx.defs}
            hints={ctx.hints}
            value={obj}
            onField={(k, x) => {
              const next = { ...obj, [k]: x };
              if (x === undefined) delete next[k];
              onChange(Object.keys(next).length ? next : undefined);
            }}
          />
        </Card>
      );
    }
    if (s.additionalProperties && typeof s.additionalProperties === "object") {
      return <MapField name={name} label={label} description={description} schema={s.additionalProperties} value={value} onChange={onChange} ctx={ctx} />;
    }
  }
  return <YamlField {...common} value={value} onChange={onChange} />;
}

/** Значение любого вида: запись YAML в одну строку (5, [a, b], {x: 1}). */
function YamlField({ label, description, value, onChange, placeholder }: { label?: string; description?: string; value: unknown; onChange: (v: unknown) => void; placeholder?: string }) {
  const [text, setText] = useState(asText(value));
  const [error, setError] = useState<string | null>(null);
  return (
    <TextInput
      label={label}
      description={description}
      placeholder={placeholder}
      ff="monospace"
      value={text}
      error={error}
      onChange={(e) => setText(e.currentTarget.value)}
      onBlur={() => {
        if (text.trim() === "") {
          setError(null);
          onChange(undefined);
          return;
        }
        try {
          onChange(parseYaml(text));
          setError(null);
        } catch {
          setError("Не понял запись: значение пишется как в YAML, например 5, текст, [a, b]");
        }
      }}
    />
  );
}

const SIMPLE = new Set(["string", "enum", "integer", "number", "boolean"]);

/** Поля объекта, если все они простые и их немного: такой список показывается строками таблицы. */
function inlineFields(schema: JsonSchema, ctx: Ctx): string[] | null {
  const props = Object.entries(schema.properties ?? {});
  if (!props.length || props.length > 4) return null;
  for (const [name, p] of props) {
    const t = typeOf(pick(resolve(p, ctx.defs), undefined, ctx).schema);
    if (!t || !SIMPLE.has(t) || MULTILINE.has(name)) return null;
  }
  return props.map(([n]) => n);
}

function ObjectList({
  name,
  label,
  description,
  schema,
  short,
  value,
  onChange,
  ctx,
}: {
  name: string;
  label?: string;
  description?: string;
  schema: JsonSchema;
  short: string | null;
  value: unknown;
  onChange: (v: unknown) => void;
  ctx: Ctx;
}) {
  const list = Array.isArray(value) ? value : [];
  const set = (next: unknown[]) => onChange(next.length ? next : undefined);
  const inline = inlineFields(schema, ctx);
  const required = new Set(schema.required ?? []);
  const update = (i: number, obj: Record<string, unknown>, k: string, x: unknown) => {
    const next = { ...obj, [k]: x };
    if (x === undefined) delete next[k];
    // Только обязательное поле — короткая запись, как пишут руками.
    const item = short ? writeShort(schema, short, next) : next;
    set(list.map((o, j) => (j === i ? item : o)));
  };
  if (inline) {
    return (
      <Stack gap={4}>
        {label && (
          <div>
            <Text size="sm" fw={500}>
              {label}
            </Text>
            {description && (
              <Text size="xs" c="dimmed">
                {description}
              </Text>
            )}
          </div>
        )}
        {list.length > 0 && (
          <Group gap={6} wrap="nowrap" pr={30}>
            {inline.map((n) => (
              <Text key={n} size="xs" c="dimmed" style={{ flex: 1 }}>
                {fieldLabel(n, resolve(schema.properties![n], ctx.defs), schema.title).label}
                {required.has(n) ? " *" : ""}
              </Text>
            ))}
          </Group>
        )}
        {list.map((item, i) => {
          const obj = short ? readShort(schema, short, item) : ((item ?? {}) as Record<string, unknown>);
          return (
            <Group key={i} gap={6} wrap="nowrap" align="center">
              {inline.map((n) => (
                <div key={n} style={{ flex: 1, minWidth: 0 }}>
                  <Field name={n} owner={schema.title} schema={schema.properties![n]} value={obj[n]} required={required.has(n)} bare ctx={ctx} onChange={(x) => update(i, obj, n, x)} />
                </div>
              ))}
              <ActionIcon variant="subtle" color="red" aria-label="Удалить" onClick={() => set(list.filter((_, j) => j !== i))}>
                <IconTrash size={16} />
              </ActionIcon>
            </Group>
          );
        })}
        <Group>
          <Button size="compact-xs" variant="light" leftSection={<IconPlus size={14} />} onClick={() => set([...list, {}])}>
            Добавить
          </Button>
        </Group>
      </Stack>
    );
  }
  return (
    <Stack gap={6}>
      {label && (
        <div>
          <Text size="sm" fw={500}>
            {label}
          </Text>
          {description && (
            <Text size="xs" c="dimmed">
              {description}
            </Text>
          )}
        </div>
      )}
      {list.map((item, i) => {
        const obj = short ? readShort(schema, short, item) : ((item ?? {}) as Record<string, unknown>);
        return (
          <Group key={i} align="flex-start" wrap="nowrap" gap={6}>
            <Card withBorder padding="xs" radius="sm" style={{ flex: 1 }}>
              <SchemaForm
                schema={schema}
                defs={ctx.defs}
                hints={ctx.hints}
                value={obj}
                compact
                onField={(k, x) => update(i, obj, k, x)}
              />
            </Card>
            <ActionIcon variant="subtle" color="red" mt={4} aria-label="Удалить" onClick={() => set(list.filter((_, j) => j !== i))}>
              <IconTrash size={16} />
            </ActionIcon>
          </Group>
        );
      })}
      <Group>
        <Button size="compact-xs" variant="light" leftSection={<IconPlus size={14} />} onClick={() => set([...list, {}])}>
          Добавить{name === "series" ? " серию" : name === "columns" ? " столбец" : ""}
        </Button>
      </Group>
    </Stack>
  );
}

function MapField({
  name,
  label,
  description,
  schema,
  value,
  onChange,
  ctx,
}: {
  name: string;
  label?: string;
  description?: string;
  schema: JsonSchema;
  value: unknown;
  onChange: (v: unknown) => void;
  ctx: Ctx;
}) {
  const obj = (value && typeof value === "object" && !Array.isArray(value) ? value : {}) as Record<string, unknown>;
  const entries = Object.entries(obj);
  const [newKey, setNewKey] = useState("");
  const set = (next: [string, unknown][]) => onChange(next.length ? Object.fromEntries(next) : undefined);
  const keyHints = name === "columns" || name === "formats" ? ctx.hints.columns : undefined;
  return (
    <Stack gap={6}>
      {label && (
        <div>
          <Text size="sm" fw={500}>
            {label}
          </Text>
          {description && (
            <Text size="xs" c="dimmed">
              {description}
            </Text>
          )}
        </div>
      )}
      {entries.map(([k, v], i) => (
        <Group key={k} align="flex-end" wrap="nowrap" gap={6}>
          <TextInput value={k} readOnly w={180} ff="monospace" />
          <div style={{ flex: 1 }}>
            <Field name={name} schema={schema} value={v} bare ctx={ctx} onChange={(x) => set(entries.map((e, j) => (j === i ? [k, x] : e)).filter((e) => e[1] !== undefined) as [string, unknown][])} />
          </div>
          <ActionIcon variant="subtle" color="red" mb={4} aria-label="Удалить" onClick={() => set(entries.filter((_, j) => j !== i))}>
            <IconTrash size={16} />
          </ActionIcon>
        </Group>
      ))}
      <Group gap={6}>
        {keyHints ? (
          <Autocomplete size="xs" placeholder="столбец" data={keyHints} value={newKey} onChange={setNewKey} w={180} />
        ) : (
          <TextInput size="xs" placeholder="ключ" value={newKey} onChange={(e) => setNewKey(e.currentTarget.value)} w={180} />
        )}
        <Button
          size="compact-xs"
          variant="light"
          disabled={!newKey || newKey in obj}
          onClick={() => {
            const sample = resolve(schema, ctx.defs);
            const t = typeOf(sample);
            const init = t === "enum" ? (sample.enum ?? [])[0] : t === "boolean" ? true : t === "integer" || t === "number" ? 0 : "";
            set([...entries, [newKey, init === "" ? newKey : init]]);
            setNewKey("");
          }}
        >
          Добавить
        </Button>
      </Group>
    </Stack>
  );
}

interface FormProps {
  schema: JsonSchema;
  defs?: Record<string, JsonSchema>;
  hints?: Hints;
  value: Record<string, unknown>;
  onField: (name: string, value: unknown) => void;
  /** Поля, которые форма не показывает (их правит конструктор сам). */
  omit?: string[];
  /** Вложенная форма (элемент списка): без надписи «Параметров нет». */
  compact?: boolean;
  /** Свои поля вместо общих: имя поля → отрисовка. */
  custom?: Record<string, (value: unknown, onChange: (value: unknown) => void) => ReactNode>;
  /** Поля, которые показываются всегда, даже если не заданы. */
  always?: string[];
}

/** Форма объекта: обязательные и заданные поля, остальные — по кнопке «Все параметры». */
export function SchemaForm({ schema, defs, hints = {}, value, onField, omit = [], compact, custom = {}, always = [] }: FormProps) {
  const [all, setAll] = useState(false);
  // поле, которое правили, не прячется, даже если его очистили (значение стало по умолчанию)
  const [touched, setTouched] = useState<string[]>([]);
  const change = (n: string, x: unknown) => {
    if (!touched.includes(n)) setTouched([...touched, n]);
    onField(n, x);
  };
  const allDefs = { ...(defs ?? {}), ...(schema.$defs ?? {}) };
  const ctx: Ctx = { defs: allDefs, hints };
  const s = resolve(schema, allDefs);
  const required = new Set(s.required ?? []);
  const names = Object.keys(s.properties ?? {}).filter((n) => !omit.includes(n));
  const primary = names.filter((n) => required.has(n) || always.includes(n) || touched.includes(n) || value[n] !== undefined);
  const rest = names.filter((n) => !primary.includes(n));
  const shown = all ? names : primary;
  return (
    <Stack gap="xs">
      {shown.map((n) =>
        custom[n] ? (
          <div key={n}>{custom[n](value[n], (x) => change(n, x))}</div>
        ) : (
          <Field key={n} name={n} owner={s.title} schema={s.properties![n]} value={value[n]} required={required.has(n)} onChange={(x) => change(n, x)} ctx={ctx} />
        ),
      )}
      {rest.length > 0 && (
        <Group>
          <Button size="compact-xs" variant="subtle" onClick={() => setAll((x) => !x)}>
            {all ? "Только заданные параметры" : `Все параметры (${rest.length})`}
          </Button>
        </Group>
      )}
      {!shown.length && !rest.length && !compact && (
        <Text size="sm" c="dimmed">
          Параметров нет.
        </Text>
      )}
    </Stack>
  );
}
