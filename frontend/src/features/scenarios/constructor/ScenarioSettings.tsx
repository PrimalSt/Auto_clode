// Сценарий целиком: название, шаблон, имя файла отчёта, общие привязки меток.
import { Card, Select, Stack, Text, TextInput, Title } from "@mantine/core";
import { useThemes } from "../../themes/queries";
import { refId, setIn, useDraft, type ScenarioDraft } from "../draft";
import type { ConstructorData } from "./context";
import { MarkerField } from "./MarkerField";
import type { JsonSchema } from "./SchemaForm";

export function ScenarioSettings({ spec, data }: { spec: ScenarioDraft; data: ConstructorData }) {
  const edit = useDraft((s) => s.edit);
  const themes = useThemes().data ?? [];
  const markersPlugin = data.blocks.find((p) => p.name === "markers")?.params_schema as JsonSchema | undefined;
  const defs = markersPlugin?.$defs ?? {};
  // Метки слайдов-образцов, которые есть в сценарии: общая привязка действует на всех.
  const used = new Set((spec.slides ?? []).map((s) => refId(s.example)).filter((x): x is number => x != null));
  const names = [...new Set((data.manifest?.slides ?? []).filter((s) => used.has(s.slide_id)).flatMap((s) => s.markers.map((m) => m.name)))];
  const common = spec.markers ?? {};
  for (const n of Object.keys(common)) if (!names.includes(n)) names.push(n);
  const set = (k: string, v: unknown) => edit((d) => setIn(d, [k], v));
  return (
    <Stack>
      <Title order={4}>Сценарий</Title>
      <TextInput label="Название" value={spec.name ?? ""} onChange={(e) => set("name", e.currentTarget.value)} withAsterisk />
      <Select
        label="Шаблон оформления"
        description="Шаблоны загружаются в разделе «Оформление»"
        data={[
          ...themes.map((t) => ({ value: t.id, label: `${t.name} (${t.id}, версия ${t.version})` })),
          ...(spec.theme && !themes.some((t) => t.id === spec.theme) ? [{ value: spec.theme, label: `${spec.theme} (нет в папке данных)` }] : []),
        ]}
        value={spec.theme ?? null}
        onChange={(v) => set("theme", v ?? undefined)}
        clearable
      />
      <TextInput
        label="Имя файла отчёта"
        description="{period} — отчётный период"
        placeholder="Отчёт_{period}"
        value={spec.output_name ?? ""}
        onChange={(e) => set("output_name", e.currentTarget.value)}
      />
      <Select
        label="Относительные периоды фильтров отсчитываются от"
        data={[
          { value: "report_period", label: "конца отчётного периода" },
          { value: "run_date", label: "даты запуска" },
        ]}
        value={(spec.settings?.relative_to as string) ?? "report_period"}
        allowDeselect={false}
        onChange={(v) => edit((d) => setIn(d, ["settings", "relative_to"], v === "report_period" ? undefined : v))}
      />
      <Card withBorder padding="sm">
        <Text fw={500}>Общие привязки меток</Text>
        <Text size="xs" c="dimmed" mb="xs">
          Действуют на всех слайдах-образцах; привязка на слайде важнее общей.
        </Text>
        {!names.length && (
          <Text size="sm" c="dimmed">
            В сценарии нет слайдов-образцов с метками.
          </Text>
        )}
        <Stack gap="xs">
          {names.map((n) => (
            <MarkerField
              key={n}
              name={n}
              value={common[n]}
              metrics={data.base.metrics ?? []}
              schema={defs.MarkerBinding ? { $ref: "#/$defs/MarkerBinding" } : undefined}
              defs={defs}
              onChange={(v) => edit((d) => setIn(d, ["markers", n], v))}
            />
          ))}
        </Stack>
      </Card>
    </Stack>
  );
}
