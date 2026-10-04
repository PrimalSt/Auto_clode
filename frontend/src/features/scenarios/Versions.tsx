// Версии сценария: что менялось и когда; сравнение с черновиком, возврат старой версии.
import { DiffEditor } from "@monaco-editor/react";
import { Button, Grid, Group, NavLink, ScrollArea, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { EDITOR_OPTIONS } from "../../shared/monaco/setup";
import { dateTime } from "../../shared/format";
import { isDirty, useDraft } from "./draft";
import { useScenarioText, useScenarioVersions } from "./queries";

export function Versions({ scenarioId }: { scenarioId: string }) {
  const versions = useScenarioVersions(scenarioId);
  const [selected, setSelected] = useState<number | null>(null);
  const list = [...(versions.data ?? [])].reverse();
  const dirty = useDraft(isDirty);
  // есть правки — с последней версией; нет — с предыдущей: видно, что поменялось в последний раз
  const current = selected ?? (dirty ? list[0]?.number : (list[1]?.number ?? list[0]?.number)) ?? null;
  const text = useScenarioText(scenarioId, current ?? undefined);
  const draft = useDraft((s) => s.text);
  const setText = useDraft((s) => s.setText);
  if (versions.error) return <ErrorAlert error={versions.error} />;
  return (
    <Grid>
      <Grid.Col span={3}>
        <ScrollArea.Autosize mah="calc(100vh - 240px)">
          {list.map((v) => (
            <NavLink
              key={v.number}
              active={v.number === current}
              onClick={() => setSelected(v.number)}
              label={`Версия ${v.number}`}
              description={
                <>
                  {dateTime(v.created_at)}
                  {v.comment ? ` · ${v.comment}` : ""}
                  {v.theme_id ? ` · шаблон ${v.theme_id} v${v.theme_version}` : ""}
                </>
              }
            />
          ))}
        </ScrollArea.Autosize>
      </Grid.Col>
      <Grid.Col span={9}>
        <Stack gap="xs">
          <Group justify="space-between">
            <Text size="sm" c="dimmed">
              Слева — версия {current}, справа — черновик (то, что сейчас в конструкторе и коде).
            </Text>
            <Button size="xs" variant="light" disabled={!text.data || text.data === draft} onClick={() => text.data && setText(text.data)}>
              Вернуть версию {current} в черновик
            </Button>
          </Group>
          <ErrorAlert error={text.error} />
          <div style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 4, overflow: "hidden" }}>
            <DiffEditor
              height="calc(100vh - 300px)"
              language="yaml"
              original={text.data ?? ""}
              modified={draft}
              // модели живут по своим адресам и не удаляются при уходе с вкладки: иначе Monaco падает,
              // разбирая сравнение после удаления моделей
              originalModelPath="file:///versions/original.yaml"
              modifiedModelPath="file:///versions/draft.yaml"
              keepCurrentOriginalModel
              keepCurrentModifiedModel
              options={{ ...EDITOR_OPTIONS, readOnly: true, renderSideBySide: true, wordWrap: "on" }}
            />
          </div>
        </Stack>
      </Grid.Col>
    </Grid>
  );
}
