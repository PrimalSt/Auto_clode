// Конструктор: структура сценария слева, правка выбранного места в середине, превью справа.
import { Alert, Box, Card, Group, Text } from "@mantine/core";
import type { Issue } from "../../shared/api/client";
import { DatasetEditor } from "./constructor/DatasetEditor";
import { InputEditor } from "./constructor/InputEditor";
import { MetricEditor } from "./constructor/MetricEditor";
import { ScenarioSettings } from "./constructor/ScenarioSettings";
import { SlideEditor } from "./constructor/SlideEditor";
import { StepEditor } from "./constructor/StepEditor";
import type { ConstructorData } from "./constructor/context";
import { previewTarget, useDraft, type ScenarioDraft } from "./draft";
import { Outline } from "./Outline";
import { NodePreview, SlidePreview } from "./Preview";

function Editor({ spec, data }: { spec: ScenarioDraft; data: ConstructorData }) {
  const path = useDraft((s) => s.selected);
  const [group, i, sub, j] = path;
  // своё состояние форм (раскрытые параметры и т. п.) у каждого узла
  const key = `${group}/${i}/${sub === "pipeline" ? j : ""}`;
  if (group === "inputs" && typeof i === "number" && sub === "pipeline" && typeof j === "number") return <StepEditor key={key} spec={spec} input={i} step={j} data={data} />;
  if (group === "inputs" && typeof i === "number") return <InputEditor key={key} spec={spec} index={i} data={data} />;
  if (group === "datasets" && typeof i === "number") return <DatasetEditor key={key} spec={spec} index={i} data={data} />;
  if (group === "metrics" && typeof i === "number") return <MetricEditor key={key} spec={spec} index={i} data={data} />;
  if (group === "slides" && typeof i === "number") return <SlideEditor key={key} spec={spec} index={i} data={data} />;
  return <ScenarioSettings spec={spec} data={data} />;
}

export function Constructor({ spec, data, issues, period }: { spec: ScenarioDraft | null; data: ConstructorData; issues: Issue[]; period: string }) {
  const parsed = useDraft((s) => s.parsed);
  const text = useDraft((s) => s.text);
  const selected = useDraft((s) => s.selected);
  if (!spec) {
    return (
      <Alert color="red" title="Код сценария не читается">
        {parsed.error} — исправьте его на вкладке «Код», и конструктор снова откроется.
      </Alert>
    );
  }
  const target = previewTarget(spec, selected);
  return (
    <Group align="flex-start" gap="md" wrap="nowrap">
      <Card withBorder padding="xs" w={260} style={{ flexShrink: 0 }}>
        <Outline spec={spec} data={data} issues={issues} />
      </Card>
      {/* своя прокрутка без горизонтальной: поля сжимаются по ширине колонки */}
      <Card withBorder padding={0} style={{ flex: "5 1 440px", minWidth: 380 }}>
        <Box p="md" style={{ maxHeight: "calc(100vh - 230px)", overflowY: "auto", overflowX: "hidden" }}>
          <Editor spec={spec} data={data} />
        </Box>
      </Card>
      <Box style={{ flex: "4 1 400px", minWidth: 340 }}>
        <Card withBorder padding="md">
          {target?.kind === "node" && <NodePreview text={text} target={target.target} period={period} />}
          {target?.kind === "slide" && <SlidePreview text={text} number={target.number} period={period} />}
          {!target && (
            <Text size="sm" c="dimmed">
              Выберите вход, шаг, набор, показатель или слайд — здесь появится превью на данных из истории.
            </Text>
          )}
        </Card>
      </Box>
    </Group>
  );
}
