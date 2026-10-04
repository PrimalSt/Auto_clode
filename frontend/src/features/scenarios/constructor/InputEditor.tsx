// Вход сценария: источник, основной вход и шаги обработки (с числом строк «было → стало»).
import { ActionIcon, Badge, Button, Card, Group, Menu, Select, Stack, Switch, Table, Text, Title, Tooltip } from "@mantine/core";
import { IconArrowDown, IconArrowUp, IconCopy, IconPlus, IconTrash } from "@tabler/icons-react";
import { count } from "../../../shared/format";
import { addIn, duplicateIn, freeId, moveIn, setIn, useDraft, type ScenarioDraft } from "../draft";
import { pluginTitle, usePreviewSteps, type ConstructorData } from "./context";
import { IdField } from "./IdField";
import { renameRefs } from "./refs";

export function InputEditor({ spec, index, data }: { spec: ScenarioDraft; index: number; data: ConstructorData }) {
  const edit = useDraft((s) => s.edit);
  const select = useDraft((s) => s.select);
  const input = spec.inputs?.[index];
  const stats = usePreviewSteps((s) => (input?.id ? s.steps[input.id] : undefined));
  if (!input) return null;
  const path = ["inputs", index];
  const pipeline = input.pipeline ?? [];
  const ids = pipeline.map((s) => s.id ?? "");
  const addStep = (type: string) => {
    const id = freeId(type, ids);
    edit((d) => addIn(d, [...path, "pipeline"], { id, type }));
    select([...path, "pipeline", pipeline.length]);
  };
  const duplicateStep = (j: number) => {
    let at = j + 1;
    edit((d) => {
      at = duplicateIn(d, [...path, "pipeline", j], ids);
    });
    select([...path, "pipeline", at]);
  };
  return (
    <Stack>
      <Title order={4}>Вход «{input.id}»</Title>
      <Group grow align="flex-start">
        <IdField
          value={input.id ?? ""}
          taken={(spec.inputs ?? []).map((i) => i.id ?? "").filter((x) => x !== input.id)}
          description="На него ссылаются наборы и показатели"
          onCommit={(id) =>
            edit((d) => {
              renameRefs(d, spec, "input", input.id ?? "", id);
              setIn(d, [...path, "id"], id);
            })
          }
        />
        <Select
          label="Источник"
          description="История загрузок этого источника"
          data={data.sources.map((s) => ({ value: s.id, label: `${s.name} (${s.id})` }))}
          value={input.source ?? null}
          searchable
          allowDeselect={false}
          onChange={(v) => v && edit((d) => setIn(d, [...path, "source"], v))}
        />
      </Group>
      <Switch
        label="Основной вход"
        description="По его истории выбирается отчётный период"
        checked={!!input.main}
        onChange={(e) => {
          const on = e.currentTarget.checked;
          edit((d) => {
            (spec.inputs ?? []).forEach((_, j) => setIn(d, ["inputs", j, "main"], on && j === index ? true : undefined));
          });
        }}
      />
      <Card withBorder padding="sm">
        <Group justify="space-between" mb="xs">
          <Text fw={500}>Шаги обработки</Text>
          <Menu position="bottom-end" withinPortal>
            <Menu.Target>
              <Button size="xs" variant="light" leftSection={<IconPlus size={14} />}>
                Добавить шаг
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              {data.steps.map((p) => (
                <Menu.Item key={p.name} onClick={() => addStep(p.name)}>
                  {p.title}
                  <Text span size="xs" c="dimmed" ml={6}>
                    {p.name}
                  </Text>
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
        </Group>
        {!pipeline.length && (
          <Text size="sm" c="dimmed">
            Шагов нет: вход — это история источника как есть. Добавьте удаление дубликатов, фильтр, формулу или свой шаг
            на Python или SQL.
          </Text>
        )}
        {!!pipeline.length && (
          <Table verticalSpacing={4} highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={40}>Вкл.</Table.Th>
                <Table.Th>Шаг</Table.Th>
                <Table.Th>Строк: было → стало</Table.Th>
                <Table.Th w={124} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {pipeline.map((st, j) => {
                const stat = stats?.find((x) => x.id === st.id);
                return (
                  <Table.Tr key={`${st.id}-${j}`} style={{ cursor: "pointer" }} onClick={() => select([...path, "pipeline", j])}>
                    <Table.Td onClick={(e) => e.stopPropagation()}>
                      <Switch
                        size="xs"
                        checked={st.enabled !== false}
                        onChange={(e) => {
                          const on = e.currentTarget.checked;
                          edit((d) => setIn(d, [...path, "pipeline", j, "enabled"], on ? undefined : false));
                        }}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" fw={500} c={st.enabled === false ? "dimmed" : undefined}>
                        {pluginTitle(data.steps, st.type)}
                      </Text>
                      <Text size="xs" c="dimmed" ff="monospace">
                        {st.id}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      {stat?.error ? (
                        <Tooltip label={stat.error} multiline w={320}>
                          <Badge color="red" variant="light">
                            ошибка
                          </Badge>
                        </Tooltip>
                      ) : stat && stat.rows_before != null ? (
                        <Text size="sm">
                          {count(stat.rows_before)} → {count(stat.rows_after)}
                        </Text>
                      ) : (
                        <Text size="xs" c="dimmed">
                          откройте превью
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td onClick={(e) => e.stopPropagation()}>
                      <Group gap={2} wrap="nowrap">
                        <ActionIcon size="sm" variant="subtle" disabled={j === 0} aria-label="Выше" onClick={() => edit((d) => moveIn(d, [...path, "pipeline"], j, j - 1))}>
                          <IconArrowUp size={14} />
                        </ActionIcon>
                        <ActionIcon size="sm" variant="subtle" disabled={j === pipeline.length - 1} aria-label="Ниже" onClick={() => edit((d) => moveIn(d, [...path, "pipeline"], j, j + 1))}>
                          <IconArrowDown size={14} />
                        </ActionIcon>
                        <ActionIcon size="sm" variant="subtle" aria-label="Дублировать" onClick={() => duplicateStep(j)}>
                          <IconCopy size={14} />
                        </ActionIcon>
                        <ActionIcon size="sm" variant="subtle" color="red" aria-label="Удалить" onClick={() => edit((d) => d.deleteIn([...path, "pipeline", j]))}>
                          <IconTrash size={14} />
                        </ActionIcon>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        )}
      </Card>
    </Stack>
  );
}
