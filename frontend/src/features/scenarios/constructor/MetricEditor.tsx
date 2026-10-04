// Показатель — одно число: агрегат входа, агрегат набора, формула, SQL или Python.
import { Alert, Group, Input, SegmentedControl, Stack, TextInput, Title } from "@mantine/core";
import { setIn, useDraft, type MetricDraft, type ScenarioDraft } from "../draft";
import { datasetColumns, inputColumns, nodeSchema, type ConstructorData } from "./context";
import { IdField } from "./IdField";
import { renameRefs } from "./refs";
import { SchemaForm } from "./SchemaForm";
import { WindowField } from "./WindowField";

type Kind = "input" | "dataset" | "formula" | "sql" | "python";

const KINDS = [
  { value: "input", label: "По входу" },
  { value: "dataset", label: "По набору" },
  { value: "formula", label: "Формула" },
  { value: "sql", label: "SQL" },
  { value: "python", label: "Python" },
];
const FIELDS: Record<Kind, string[]> = {
  input: ["input", "window", "fn", "column", "where", "compare"],
  dataset: ["dataset", "fn", "column", "where", "compare"],
  formula: ["formula", "compare"],
  sql: ["query", "window", "compare"],
  python: ["code", "inputs", "frame", "window", "compare"],
};
const ALL = ["input", "dataset", "window", "fn", "column", "where", "formula", "query", "code", "inputs", "frame", "compare"];

export function metricKind(m: MetricDraft): Kind {
  if (m.formula !== undefined) return "formula";
  if (m.query !== undefined) return "sql";
  if (m.code !== undefined) return "python";
  return m.dataset !== undefined ? "dataset" : "input";
}

export function MetricEditor({ spec, index, data }: { spec: ScenarioDraft; index: number; data: ConstructorData }) {
  const edit = useDraft((s) => s.edit);
  const m = spec.metrics?.[index];
  const node = nodeSchema(data.schema, "MetricSpec");
  if (!m) return null;
  const path = ["metrics", index];
  const kind = metricKind(m);
  const columns =
    kind === "dataset"
      ? datasetColumns(spec.datasets?.find((d) => d.id === m.dataset), spec, data.sources, data.seen)
      : inputColumns(spec.inputs?.find((i) => i.id === m.input), data.sources, data.seen);
  const setLabel = (v: string) => edit((d) => setIn(d, [...path, "label"], v));
  return (
    <Stack>
      <Title order={4}>Показатель «{m.label || m.id}»</Title>
      <Group grow align="flex-start">
        <IdField
          value={m.id ?? ""}
          taken={(spec.metrics ?? []).map((x) => x.id ?? "").filter((x) => x !== m.id)}
          description="Метки и тексты слайдов берут его по id"
          onCommit={(id) =>
            edit((d) => {
              renameRefs(d, spec, "metric", m.id ?? "", id);
              setIn(d, [...path, "id"], id);
            })
          }
        />
        <TextInput label="Подпись" value={m.label ?? ""} onChange={(e) => setLabel(e.currentTarget.value)} />
      </Group>
      <Input.Wrapper label="Как считается">
        <SegmentedControl
          fullWidth
          data={KINDS}
          value={kind}
          onChange={(k) =>
            edit((d) => {
              const keep = FIELDS[k as Kind];
              for (const f of ALL) if (!keep.includes(f)) setIn(d, [...path, f], undefined);
              if (k === "input" || k === "dataset") {
                setIn(d, [...path, k], k === "input" ? spec.inputs?.[0]?.id : spec.datasets?.[0]?.id);
                setIn(d, [...path, "fn"], m.fn ?? "sum");
              }
              if (k === "formula") setIn(d, [...path, "formula"], "0");
              if (k === "sql") setIn(d, [...path, "query"], `SELECT sum(amount) FROM ${spec.inputs?.[0]?.id ?? "data"}`);
              if (k === "python") {
                setIn(d, [...path, "code"], "def value(tables, ctx):\n    return 0\n");
                setIn(d, [...path, "inputs"], [spec.inputs?.[0]?.id ?? ""]);
              }
            })
          }
        />
      </Input.Wrapper>
      {!node && <Alert color="yellow">Схема сценария не загрузилась: показатель можно править в коде.</Alert>}
      {node && (
        <SchemaForm
          schema={node.schema}
          defs={node.defs}
          hints={{ ...data.base, columns }}
          value={m as Record<string, unknown>}
          omit={["id", "label", ...ALL.filter((f) => !FIELDS[kind].includes(f))]}
          always={FIELDS[kind].filter((f) => f !== "compare" && f !== "where" && f !== "frame")}
          custom={{ window: (v, set) => <WindowField value={v} onChange={set} windows={data.windows} /> }}
          onField={(k, v) => edit((d) => setIn(d, [...path, k], v))}
        />
      )}
    </Stack>
  );
}
