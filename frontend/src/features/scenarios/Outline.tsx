// Структура сценария: входы с шагами, наборы, показатели, слайды. Выбор места — правка и превью.
import { ActionIcon, Badge, Box, Group, Menu, NavLink, ScrollArea, Text, Tooltip } from "@mantine/core";
import { modals } from "@mantine/modals";
import { IconArrowDown, IconArrowUp, IconCopy, IconDots, IconPlus, IconTrash } from "@tabler/icons-react";
import type { ReactNode } from "react";
import type { Issue } from "../../shared/api/client";
import { addIn, duplicateIn, freeId, issuePath, moveIn, slug, useDraft, type Path, type ScenarioDraft } from "./draft";
import { pluginTitle, type ConstructorData } from "./constructor/context";
import { slideNumber, slideTitle } from "./constructor/SlideEditor";

const same = (a: Path, b: Path) => a.length === b.length && a.every((x, i) => x === b[i]);

function IssueBadge({ issues }: { issues: Issue[] }) {
  if (!issues.length) return null;
  const errors = issues.filter((i) => i.level === "error").length;
  return (
    <Tooltip label={issues.map((i) => i.message).join("\n")} multiline w={360} withinPortal>
      <Badge size="xs" color={errors ? "red" : "yellow"} variant="filled" circle>
        {errors || issues.length}
      </Badge>
    </Tooltip>
  );
}

function Section({ title, onAdd, addMenu, children }: { title: string; onAdd?: () => void; addMenu?: ReactNode; children: ReactNode }) {
  return (
    <Box mb="sm">
      <Group justify="space-between" px="xs" mb={2}>
        <Text size="xs" fw={700} c="dimmed" tt="uppercase">
          {title}
        </Text>
        {addMenu ?? (
          <ActionIcon size="sm" variant="subtle" aria-label={`Добавить: ${title}`} onClick={onAdd}>
            <IconPlus size={14} />
          </ActionIcon>
        )}
      </Group>
      {children}
    </Box>
  );
}

function ItemActions({ path, total, onDelete, ids = [] }: { path: Path; total: number; onDelete: () => void; ids?: (string | undefined)[] }) {
  const edit = useDraft((s) => s.edit);
  const select = useDraft((s) => s.select);
  const i = path[path.length - 1] as number;
  const list = path.slice(0, -1);
  const duplicate = () => {
    let at = i + 1;
    edit((d) => {
      at = duplicateIn(d, path, ids);
    });
    select([...list, at]);
  };
  return (
    <Menu position="bottom-end" withinPortal>
      <Menu.Target>
        <ActionIcon size="xs" variant="subtle" onClick={(e) => e.stopPropagation()} aria-label="Действия">
          <IconDots size={14} />
        </ActionIcon>
      </Menu.Target>
      <Menu.Dropdown onClick={(e) => e.stopPropagation()}>
        <Menu.Item leftSection={<IconArrowUp size={14} />} disabled={i === 0} onClick={() => (edit((d) => moveIn(d, list, i, i - 1)), select([...list, i - 1]))}>
          Выше
        </Menu.Item>
        <Menu.Item leftSection={<IconArrowDown size={14} />} disabled={i === total - 1} onClick={() => (edit((d) => moveIn(d, list, i, i + 1)), select([...list, i + 1]))}>
          Ниже
        </Menu.Item>
        <Menu.Item leftSection={<IconCopy size={14} />} onClick={duplicate}>
          Дублировать
        </Menu.Item>
        <Menu.Item leftSection={<IconTrash size={14} />} color="red" onClick={onDelete}>
          Удалить
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}

export function Outline({ spec, data, issues }: { spec: ScenarioDraft; data: ConstructorData; issues: Issue[] }) {
  const selected = useDraft((s) => s.selected);
  const select = useDraft((s) => s.select);
  const edit = useDraft((s) => s.edit);
  const byPath = new Map<string, Issue[]>();
  for (const is of issues) {
    const p = issuePath(spec, is.node) ?? [];
    const k = JSON.stringify(p);
    byPath.set(k, [...(byPath.get(k) ?? []), is]);
  }
  const issuesAt = (p: Path) => byPath.get(JSON.stringify(p)) ?? [];
  const remove = (path: Path, what: string) =>
    modals.openConfirmModal({
      title: `Удалить ${what}?`,
      children: <Text size="sm">Удаление попадёт в сценарий при сохранении; до этого его можно отменить кнопкой «Отменить правки».</Text>,
      labels: { confirm: "Удалить", cancel: "Отмена" },
      confirmProps: { color: "red" },
      onConfirm: () => {
        edit((d) => d.deleteIn(path));
        select([]);
      },
    });
  const add = (group: "inputs" | "datasets" | "metrics", value: Record<string, unknown>) => {
    const n = (spec[group] ?? []).length;
    edit((d) => addIn(d, [group], value));
    select([group, n]);
  };
  const inputs = spec.inputs ?? [];
  const datasets = spec.datasets ?? [];
  const metrics = spec.metrics ?? [];
  const slides = spec.slides ?? [];
  const firstInput = inputs[0]?.id;
  const addSlide = (value: Record<string, unknown>) => {
    edit((d) => addIn(d, ["slides"], value));
    select(["slides", slides.length]);
  };
  const usedExamples = new Set(slides.map((s) => (typeof s.example === "number" ? s.example : s.example?.id)));
  return (
    <ScrollArea.Autosize mah="calc(100vh - 230px)" type="auto">
      <NavLink label="Сценарий" description={spec.name} active={same(selected, [])} onClick={() => select([])} mb="sm" rightSection={<IssueBadge issues={issuesAt([])} />} />
      <Section
        title="Входы"
        onAdd={() => {
          const src = data.sources.find((s) => !inputs.some((i) => i.source === s.id)) ?? data.sources[0];
          add("inputs", { id: freeId(slug(src?.id ?? "input", "input"), inputs.map((i) => i.id)), source: src?.id ?? "", ...(inputs.length ? {} : { main: true }) });
        }}
      >
        {inputs.map((inp, i) => (
          <NavLink
            key={`${inp.id}-${i}`}
            label={inp.id}
            description={inp.main ? `основной · ${inp.source}` : inp.source}
            active={same(selected, ["inputs", i])}
            opened
            onClick={() => select(["inputs", i])}
            rightSection={
              <Group gap={4} wrap="nowrap">
                <IssueBadge issues={issuesAt(["inputs", i])} />
                <ItemActions path={["inputs", i]} total={inputs.length} ids={inputs.map((x) => x.id)} onDelete={() => remove(["inputs", i], `вход «${inp.id}»`)} />
              </Group>
            }
            childrenOffset={14}
          >
            {(inp.pipeline ?? []).map((st, j) => (
              <NavLink
                key={`${st.id}-${j}`}
                label={pluginTitle(data.steps, st.type)}
                description={st.id}
                c={st.enabled === false ? "dimmed" : undefined}
                active={same(selected, ["inputs", i, "pipeline", j])}
                onClick={() => select(["inputs", i, "pipeline", j])}
                rightSection={<IssueBadge issues={issuesAt(["inputs", i, "pipeline", j])} />}
              />
            ))}
          </NavLink>
        ))}
      </Section>
      <Section title="Наборы данных" onAdd={() => add("datasets", { id: freeId("dataset", datasets.map((d) => d.id)), input: firstInput ?? "" })}>
        {datasets.map((ds, i) => (
          <NavLink
            key={`${ds.id}-${i}`}
            label={ds.label || ds.id}
            description={ds.label ? ds.id : undefined}
            active={same(selected, ["datasets", i])}
            onClick={() => select(["datasets", i])}
            rightSection={
              <Group gap={4} wrap="nowrap">
                <IssueBadge issues={issuesAt(["datasets", i])} />
                <ItemActions path={["datasets", i]} total={datasets.length} ids={datasets.map((x) => x.id)} onDelete={() => remove(["datasets", i], `набор «${ds.id}»`)} />
              </Group>
            }
          />
        ))}
      </Section>
      <Section title="Показатели" onAdd={() => add("metrics", { id: freeId("metric", metrics.map((m) => m.id)), input: firstInput ?? "", fn: "count" })}>
        {metrics.map((m, i) => (
          <NavLink
            key={`${m.id}-${i}`}
            label={m.label || m.id}
            description={m.label ? m.id : undefined}
            active={same(selected, ["metrics", i])}
            onClick={() => select(["metrics", i])}
            rightSection={
              <Group gap={4} wrap="nowrap">
                <IssueBadge issues={issuesAt(["metrics", i])} />
                <ItemActions path={["metrics", i]} total={metrics.length} ids={metrics.map((x) => x.id)} onDelete={() => remove(["metrics", i], `показатель «${m.id}»`)} />
              </Group>
            }
          />
        ))}
      </Section>
      <Section
        title="Слайды"
        addMenu={
          <Menu position="bottom-end" withinPortal>
            <Menu.Target>
              <ActionIcon size="sm" variant="subtle" aria-label="Добавить слайд">
                <IconPlus size={14} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown mah={420} style={{ overflowY: "auto" }}>
              <Menu.Item onClick={() => addSlide({ layout: "title_and_content", blocks: [{ type: "text", slot: "title", text: "Заголовок" }] })}>
                Слайд из макета
              </Menu.Item>
              {(data.manifest?.slides ?? []).length > 0 && <Menu.Label>Слайды шаблона</Menu.Label>}
              {(data.manifest?.slides ?? []).map((s) => (
                <Menu.Item key={s.slide_id} onClick={() => addSlide({ example: s.slide_id })} rightSection={usedExamples.has(s.slide_id) ? <Text size="xs" c="dimmed">уже есть</Text> : null}>
                  {s.number}. {s.title ?? s.layout_name}
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
        }
      >
        {slides.map((_, i) => {
          const n = slideNumber(spec, i);
          return (
            <NavLink
              key={i}
              label={slideTitle(spec, i, data.manifest)}
              description={n ? `слайд ${n}` : "выключен"}
              c={n ? undefined : "dimmed"}
              active={same(selected, ["slides", i])}
              onClick={() => select(["slides", i])}
              rightSection={
                <Group gap={4} wrap="nowrap">
                  <IssueBadge issues={issuesAt(["slides", i])} />
                  <ItemActions path={["slides", i]} total={slides.length} onDelete={() => remove(["slides", i], "слайд")} />
                </Group>
              }
            />
          );
        })}
      </Section>
    </ScrollArea.Autosize>
  );
}
