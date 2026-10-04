// Слайд: слайд-образец шаблона (метки, графики, таблицы привязываются к показателям и наборам)
// или слайд из макета (блоки: текст, график, таблица в областях макета).
import { ActionIcon, Alert, Badge, Button, Card, Group, Menu, SegmentedControl, Select, Stack, Switch, Text, Title } from "@mantine/core";
import { IconArrowDown, IconArrowUp, IconPlus, IconTrash } from "@tabler/icons-react";
import type { Schemas } from "../../../shared/api/client";
import { LAYOUT_ROLES } from "../../../shared/labels";
import { addIn, moveIn, refId, setIn, useDraft, type BlockDraft, type ScenarioDraft } from "../draft";
import { datasetColumns, pluginTitle, type ConstructorData } from "./context";
import { MarkerField } from "./MarkerField";
import { SchemaForm, type JsonSchema } from "./SchemaForm";

type TemplateSlide = Schemas["TemplateSlideInfo"];

const BLOCK_COMMON = ["type", "type_version", "id", "slot", "shape"];
const SHAPE_BLOCKS = new Set(["chart_fill", "table_fill", "markers"]);

/** Номер слайда в отчёте (среди включённых) или null, если слайд выключен. */
export function slideNumber(spec: ScenarioDraft, index: number): number | null {
  const slides = spec.slides ?? [];
  if (slides[index]?.enabled === false) return null;
  return slides.slice(0, index + 1).filter((s) => s.enabled !== false).length;
}

export function slideTitle(spec: ScenarioDraft, index: number, manifest: Schemas["ThemeManifest"] | null): string {
  const s = spec.slides?.[index];
  if (!s) return "";
  const ex = refId(s.example);
  if (ex != null) {
    const t = manifest?.slides.find((x) => x.slide_id === ex);
    return t ? `Слайд шаблона ${t.number}${t.title ? `: ${t.title}` : ""}` : `Слайд шаблона ${ex}`;
  }
  const title = s.blocks?.find((b) => b.slot === "title" && typeof b.text === "string")?.text as string | undefined;
  return title ? title.replace(/\{\{.*?\}\}/g, "…") : `Макет «${LAYOUT_ROLES[s.layout ?? ""] ?? s.layout ?? "?"}»`;
}

function BlockForm({ spec, path, block, data }: { spec: ScenarioDraft; path: (string | number)[]; block: BlockDraft; data: ConstructorData }) {
  const edit = useDraft((s) => s.edit);
  const plugin = data.blocks.find((p) => p.name === block.type);
  const params = Object.fromEntries(Object.entries(block).filter(([k]) => !BLOCK_COMMON.includes(k)));
  const columns = datasetColumns(spec.datasets?.find((d) => d.id === block.dataset), spec, data.sources, data.seen);
  if (!plugin?.params_schema) {
    return (
      <Text size="sm" c="dimmed">
        Модуля блока «{block.type}» нет среди работающих: параметры правятся в коде сценария.
      </Text>
    );
  }
  return (
    <SchemaForm
      schema={plugin.params_schema as JsonSchema}
      hints={{ ...data.base, columns }}
      value={params}
      onField={(k, v) => edit((d) => setIn(d, [...path, k], v))}
    />
  );
}

function ShapeBinding({
  spec,
  index,
  kind,
  shapeId,
  title,
  details,
  data,
}: {
  spec: ScenarioDraft;
  index: number;
  kind: "chart_fill" | "table_fill";
  shapeId: number;
  title: string;
  details: string;
  data: ConstructorData;
}) {
  const edit = useDraft((s) => s.edit);
  const slide = spec.slides![index];
  const blocks = slide.blocks ?? [];
  const j = blocks.findIndex((b) => b.type === kind && refId(b.shape) === shapeId);
  const kept = (slide.keep ?? []).includes(shapeId);
  const path = ["slides", index];
  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" mb={j >= 0 ? "xs" : 0}>
        <div>
          <Text size="sm" fw={500}>
            {title}
          </Text>
          <Text size="xs" c="dimmed">
            {details} · фигура {shapeId}
          </Text>
        </div>
        <Group gap="xs">
          {j >= 0 && (
            <Button size="compact-xs" variant="subtle" color="red" onClick={() => edit((d) => d.deleteIn([...path, "blocks", j]))}>
              Отвязать
            </Button>
          )}
          {j < 0 && kept && (
            <>
              <Badge variant="light" color="gray">
                как в шаблоне
              </Badge>
              <Button size="compact-xs" variant="subtle" onClick={() => edit((d) => setIn(d, [...path, "keep"], (slide.keep ?? []).filter((x) => x !== shapeId)))}>
                Заполнять
              </Button>
            </>
          )}
          {j < 0 && !kept && (
            <>
              <Badge variant="light" color="yellow">
                не привязан
              </Badge>
              <Button
                size="compact-xs"
                variant="light"
                onClick={() => edit((d) => addIn(d, [...path, "blocks"], { type: kind, shape: shapeId, dataset: spec.datasets?.[0]?.id ?? "", ...(kind === "chart_fill" ? { categories: "" } : {}) }))}
              >
                Привязать к набору
              </Button>
              <Button size="compact-xs" variant="subtle" onClick={() => edit((d) => setIn(d, [...path, "keep"], [...(slide.keep ?? []), shapeId]))}>
                Оставить как в шаблоне
              </Button>
            </>
          )}
        </Group>
      </Group>
      {j >= 0 && <BlockForm spec={spec} path={[...path, "blocks", j]} block={blocks[j]} data={data} />}
    </Card>
  );
}

function ExampleBindings({ spec, index, slide, data }: { spec: ScenarioDraft; index: number; slide: TemplateSlide; data: ConstructorData }) {
  const edit = useDraft((s) => s.edit);
  const own = spec.slides![index].markers ?? {};
  const common = spec.markers ?? {};
  const markersPlugin = data.blocks.find((p) => p.name === "markers")?.params_schema as JsonSchema | undefined;
  const defs = markersPlugin?.$defs ?? {};
  const bindingSchema = defs.MarkerBinding ? { $ref: "#/$defs/MarkerBinding" } : undefined;
  const names = [...new Set(slide.markers.map((m) => m.name))];
  const problems = Object.fromEntries(slide.markers.filter((m) => !m.replaceable).map((m) => [m.name, m.reason ?? "Метку нельзя заменить"]));
  const unbound =
    names.filter((n) => own[n] === undefined && common[n] === undefined).length +
    slide.charts.filter((c) => !(spec.slides![index].blocks ?? []).some((b) => refId(b.shape) === c.shape_id) && !(spec.slides![index].keep ?? []).includes(c.shape_id)).length +
    slide.tables.filter((t) => !(spec.slides![index].blocks ?? []).some((b) => refId(b.shape) === t.shape_id) && !(spec.slides![index].keep ?? []).includes(t.shape_id)).length;
  return (
    <Stack>
      {unbound > 0 ? (
        <Alert color="yellow" variant="light">
          Не привязано: {unbound}. Непривязанная метка остановит сборку отчёта; график или таблицу можно оставить как в шаблоне.
        </Alert>
      ) : (
        <Text size="sm" c="teal">
          Всё на слайде привязано.
        </Text>
      )}
      {names.length > 0 && (
        <Card withBorder padding="sm">
          <Text fw={500} mb="xs">
            Метки
          </Text>
          <Stack gap="xs">
            {names.map((n) => (
              <MarkerField
                key={n}
                name={n}
                value={own[n]}
                inherited={common[n]}
                problem={problems[n]}
                metrics={data.base.metrics ?? []}
                schema={bindingSchema}
                defs={defs}
                onChange={(v) => edit((d) => setIn(d, ["slides", index, "markers", n], v))}
              />
            ))}
          </Stack>
        </Card>
      )}
      {slide.charts.map((c) => (
        <ShapeBinding
          key={c.shape_id}
          spec={spec}
          index={index}
          kind="chart_fill"
          shapeId={c.shape_id}
          title={`График «${c.shape_name}»`}
          details={`${c.chart_type}, серий: ${c.groups.reduce((a, g) => a + g.series.length, 0)}, категорий: ${c.categories}`}
          data={data}
        />
      ))}
      {slide.tables.map((t) => (
        <ShapeBinding
          key={t.shape_id}
          spec={spec}
          index={index}
          kind="table_fill"
          shapeId={t.shape_id}
          title={`Таблица «${t.shape_name}»`}
          details={`${t.rows} × ${t.cols}${t.header.length ? `: ${t.header.join(" | ")}` : ""}`}
          data={data}
        />
      ))}
    </Stack>
  );
}

function LayoutBlocks({ spec, index, data }: { spec: ScenarioDraft; index: number; data: ConstructorData }) {
  const edit = useDraft((s) => s.edit);
  const slide = spec.slides![index];
  const blocks = slide.blocks ?? [];
  const role = data.manifest?.roles.find((r) => r.role === slide.layout);
  const slots = role?.slots.map((s) => s.name) ?? ["title", "body"];
  const path = ["slides", index, "blocks"];
  const kinds = data.blocks.filter((p) => !SHAPE_BLOCKS.has(p.name));
  return (
    <Stack>
      {blocks.map((b, j) => (
        <Card key={j} withBorder padding="sm">
          <Group justify="space-between" mb="xs">
            <Group gap="xs">
              <Text fw={500}>{pluginTitle(data.blocks, b.type)}</Text>
              <Select
                size="xs"
                w={140}
                data={[...new Set([...slots, b.slot ?? "body"])]}
                value={b.slot ?? "body"}
                onChange={(v) => v && edit((d) => setIn(d, [...path, j, "slot"], v === "body" ? undefined : v))}
                aria-label="Область макета"
              />
            </Group>
            <Group gap={2}>
              <ActionIcon size="sm" variant="subtle" disabled={j === 0} aria-label="Выше" onClick={() => edit((d) => moveIn(d, path, j, j - 1))}>
                <IconArrowUp size={14} />
              </ActionIcon>
              <ActionIcon size="sm" variant="subtle" disabled={j === blocks.length - 1} aria-label="Ниже" onClick={() => edit((d) => moveIn(d, path, j, j + 1))}>
                <IconArrowDown size={14} />
              </ActionIcon>
              <ActionIcon size="sm" variant="subtle" color="red" aria-label="Удалить" onClick={() => edit((d) => d.deleteIn([...path, j]))}>
                <IconTrash size={14} />
              </ActionIcon>
            </Group>
          </Group>
          <BlockForm spec={spec} path={[...path, j]} block={b} data={data} />
        </Card>
      ))}
      <Menu position="bottom-start">
        <Menu.Target>
          <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} w="fit-content">
            Добавить блок
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          {kinds.map((p) => (
            <Menu.Item
              key={p.name}
              onClick={() => {
                const used = new Set(blocks.map((b) => b.slot ?? "body"));
                const slot = slots.find((s) => !used.has(s) && s !== "title") ?? (p.name === "text" && !used.has("title") ? "title" : "body");
                edit((d) => addIn(d, path, { type: p.name, ...(slot !== "body" ? { slot } : {}), ...(p.name === "text" ? { text: "" } : { dataset: spec.datasets?.[0]?.id ?? "" }) }));
              }}
            >
              {p.title}
            </Menu.Item>
          ))}
        </Menu.Dropdown>
      </Menu>
    </Stack>
  );
}

export function SlideEditor({ spec, index, data }: { spec: ScenarioDraft; index: number; data: ConstructorData }) {
  const edit = useDraft((s) => s.edit);
  const slide = spec.slides?.[index];
  if (!slide) return null;
  const path = ["slides", index];
  const ex = refId(slide.example);
  const kind = ex != null ? "example" : "layout";
  const manifest = data.manifest;
  const template = manifest?.slides.find((s) => s.slide_id === ex) ?? null;
  const number = slideNumber(spec, index);
  return (
    <Stack>
      <div>
        <Title order={4}>{slideTitle(spec, index, manifest)}</Title>
        <Text size="sm" c="dimmed">
          {number ? `Слайд ${number} отчёта` : "Слайд выключен: в отчёт не попадает"}
        </Text>
      </div>
      {data.themeMissing && (
        <Alert color="yellow">
          Шаблон сценария не из папки данных или не найден: слайды-образцы и макеты не видны. Выберите шаблон в настройках
          сценария (раздел «Оформление» загружает шаблоны).
        </Alert>
      )}
      <Group align="flex-end">
        <SegmentedControl
          data={[
            { value: "example", label: "Слайд шаблона" },
            { value: "layout", label: "Из макета" },
          ]}
          value={kind}
          onChange={(k) =>
            edit((d) => {
              setIn(d, [...path, "blocks"], undefined);
              setIn(d, [...path, "markers"], undefined);
              setIn(d, [...path, "keep"], undefined);
              if (k === "example") {
                setIn(d, [...path, "layout"], undefined);
                setIn(d, [...path, "example"], manifest?.slides[0]?.slide_id ?? 256);
              } else {
                setIn(d, [...path, "example"], undefined);
                setIn(d, [...path, "layout"], "title_and_content");
              }
            })
          }
        />
        <Switch
          label="В отчёте"
          checked={slide.enabled !== false}
          onChange={(e) => {
            const on = e.currentTarget.checked;
            edit((d) => setIn(d, [...path, "enabled"], on ? undefined : false));
          }}
        />
      </Group>
      {kind === "example" && (
        <Select
          label="Слайд шаблона"
          data={[
            ...(manifest?.slides ?? []).map((s) => ({ value: String(s.slide_id), label: `${s.number}. ${s.title ?? s.layout_name}` })),
            ...(template || ex == null ? [] : [{ value: String(ex), label: `id ${ex} (нет в шаблоне)` }]),
          ]}
          value={ex != null ? String(ex) : null}
          allowDeselect={false}
          onChange={(v) => v && edit((d) => setIn(d, [...path, "example"], Number(v)))}
        />
      )}
      {kind === "layout" && (
        <Select
          label="Макет"
          description="Роль макета в шаблоне (раздел «Оформление» → «Роли макетов»)"
          data={[
            ...(manifest?.roles ?? []).map((r) => ({ value: r.role, label: `${LAYOUT_ROLES[r.role] ?? r.role} — ${r.layout_name}` })),
            ...(manifest?.roles.some((r) => r.role === slide.layout) || !slide.layout ? [] : [{ value: slide.layout, label: slide.layout }]),
          ]}
          value={slide.layout ?? null}
          allowDeselect={false}
          onChange={(v) => v && edit((d) => setIn(d, [...path, "layout"], v))}
        />
      )}
      {kind === "example" && template && <ExampleBindings spec={spec} index={index} slide={template} data={data} />}
      {kind === "example" && !template && manifest && <Alert color="red">Слайда с id {ex} нет в шаблоне.</Alert>}
      {kind === "layout" && <LayoutBlocks spec={spec} index={index} data={data} />}
    </Stack>
  );
}
