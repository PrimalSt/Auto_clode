// Шаг обработки входа: параметры модуля шага по его схеме.
import { Alert, Group, Stack, Switch, Text, Title } from "@mantine/core";
import { setIn, useDraft, type ScenarioDraft } from "../draft";
import { inputColumns, pluginTitle, type ConstructorData } from "./context";
import { IdField } from "./IdField";
import { SchemaForm, type JsonSchema } from "./SchemaForm";

const COMMON = ["id", "type", "type_version", "enabled"];

export function StepEditor({ spec, input: i, step: j, data }: { spec: ScenarioDraft; input: number; step: number; data: ConstructorData }) {
  const edit = useDraft((s) => s.edit);
  const input = spec.inputs?.[i];
  const step = input?.pipeline?.[j];
  if (!input || !step) return null;
  const path = ["inputs", i, "pipeline", j];
  const plugin = data.steps.find((p) => p.name === step.type);
  const params = Object.fromEntries(Object.entries(step).filter(([k]) => !COMMON.includes(k)));
  const columns = inputColumns({ ...input, pipeline: input.pipeline?.slice(0, j) }, data.sources, data.seen);
  return (
    <Stack>
      <div>
        <Title order={4}>{pluginTitle(data.steps, step.type)}</Title>
        <Text size="sm" c="dimmed">
          Шаг входа «{input.id}» · модуль <code>{step.type}</code>
        </Text>
      </div>
      <Group grow align="flex-end">
        <IdField
          value={step.id ?? ""}
          taken={(input.pipeline ?? []).map((s) => s.id ?? "").filter((x) => x !== step.id)}
          description="Имя шага в превью и журнале запуска; ссылок на шаг в сценарии нет"
          onCommit={(id) => edit((d) => setIn(d, [...path, "id"], id))}
        />
        <Switch
          label="Шаг включён"
          checked={step.enabled !== false}
          onChange={(e) => {
            const on = e.currentTarget.checked;
            edit((d) => setIn(d, [...path, "enabled"], on ? undefined : false));
          }}
        />
      </Group>
      {!plugin && (
        <Alert color="yellow" title="Модуль шага не найден">
          Шага «{step.type}» нет среди работающих модулей (экран «Модули»). Его параметры можно править в коде сценария.
        </Alert>
      )}
      {plugin?.params_schema && (
        <SchemaForm
          schema={plugin.params_schema as JsonSchema}
          hints={{ ...data.base, columns }}
          value={params}
          onField={(k, v) => edit((d) => setIn(d, [...path, k], v))}
        />
      )}
    </Stack>
  );
}
