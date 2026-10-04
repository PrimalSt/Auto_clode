// Набор данных: вход → окно → фильтр → группировка → агрегаты → сравнение → расчёты →
// сортировка → топ-N; или запрос SQL, или код на Python.
import { Alert, Group, SegmentedControl, Stack, Text, TextInput, Title } from "@mantine/core";
import { setIn, useDraft, type ScenarioDraft } from "../draft";
import { inputColumns, nodeSchema, type ConstructorData } from "./context";
import { IdField } from "./IdField";
import { renameRefs } from "./refs";
import { SchemaForm } from "./SchemaForm";
import { WindowField } from "./WindowField";

const KINDS = [
  { value: "table", label: "Конструктор" },
  { value: "sql", label: "SQL" },
  { value: "python", label: "Python" },
];
const OMIT: Record<string, string[]> = {
  table: ["id", "label", "type", "query", "code", "inputs", "frame"],
  sql: ["id", "label", "type", "input", "where", "group_by", "aggregate", "columns", "compare", "derive", "pivot", "others", "code", "inputs", "frame"],
  python: ["id", "label", "type", "input", "where", "group_by", "aggregate", "columns", "compare", "derive", "pivot", "others", "query"],
};
const ONLY: Record<string, string[]> = {
  table: ["query", "code", "inputs", "frame"],
  sql: ["input", "where", "group_by", "aggregate", "columns", "compare", "derive", "pivot", "others", "code", "inputs", "frame"],
  python: ["input", "where", "group_by", "aggregate", "columns", "compare", "derive", "pivot", "others", "query"],
};

export function DatasetEditor({ spec, index, data }: { spec: ScenarioDraft; index: number; data: ConstructorData }) {
  const edit = useDraft((s) => s.edit);
  const ds = spec.datasets?.[index];
  const node = nodeSchema(data.schema, "DatasetSpec");
  if (!ds) return null;
  const path = ["datasets", index];
  const kind = ds.type ?? (ds.query !== undefined ? "sql" : ds.code !== undefined ? "python" : "table");
  const columns = inputColumns(spec.inputs?.find((i) => i.id === ds.input), data.sources, data.seen);
  const setLabel = (v: string) => edit((d) => setIn(d, [...path, "label"], v));
  return (
    <Stack>
      <Title order={4}>Набор «{ds.label || ds.id}»</Title>
      <Group grow align="flex-start">
        <IdField
          value={ds.id ?? ""}
          taken={(spec.datasets ?? []).map((d) => d.id ?? "").filter((x) => x !== ds.id)}
          description="На него ссылаются слайды и показатели"
          onCommit={(id) =>
            edit((d) => {
              renameRefs(d, spec, "dataset", ds.id ?? "", id);
              setIn(d, [...path, "id"], id);
            })
          }
        />
        <TextInput label="Подпись" value={ds.label ?? ""} onChange={(e) => setLabel(e.currentTarget.value)} />
      </Group>
      <SegmentedControl
        data={KINDS}
        value={kind}
        onChange={(k) =>
          edit((d) => {
            for (const f of ONLY[k]) setIn(d, [...path, f], undefined);
            setIn(d, [...path, "type"], k === "table" && ds.type === undefined ? undefined : k);
            if (k === "sql") setIn(d, [...path, "query"], `SELECT *\nFROM ${spec.inputs?.[0]?.id ?? "data"}`);
            if (k === "python") {
              setIn(d, [...path, "code"], "def build(tables, ctx):\n    df = tables[\"" + (spec.inputs?.[0]?.id ?? "data") + "\"]\n    return df\n");
              setIn(d, [...path, "inputs"], [spec.inputs?.[0]?.id ?? ""]);
            }
          })
        }
      />
      {kind === "sql" && (
        <Text size="xs" c="dimmed">
          Запрос DuckDB к входам и другим наборам по их id; окно применяется к каждому входу до запроса.
        </Text>
      )}
      {!node && <Alert color="yellow">Схема сценария не загрузилась: набор можно править в коде.</Alert>}
      {node && (
        <SchemaForm
          schema={node.schema}
          defs={node.defs}
          hints={{ ...data.base, columns }}
          value={ds as Record<string, unknown>}
          omit={OMIT[kind]}
          always={kind === "table" ? ["input", "window", "group_by", "aggregate"] : ["window"]}
          custom={{ window: (v, set) => <WindowField value={v} onChange={set} windows={data.windows} /> }}
          onField={(k, v) => edit((d) => setIn(d, [...path, k], v))}
        />
      )}
    </Stack>
  );
}
