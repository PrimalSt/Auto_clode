// Сценарии: список и новый сценарий (источники, основной вход, шаблон, слайды шаблона).
import { Button, Checkbox, Group, Modal, Radio, Select, Stack, Table, Text, TextInput } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconPlus } from "@tabler/icons-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { stringify } from "yaml";
import { api, unwrap } from "../../shared/api/client";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { Page } from "../../shared/components/Page";
import { dateTime } from "../../shared/format";
import { useSources } from "../sources/queries";
import { useTheme, useThemes } from "../themes/queries";
import { freeId, slug } from "./draft";
import { useScenarios } from "./queries";

/** Текст нового сценария: входы по выбранным источникам и слайды шаблона. */
export function newScenarioText(o: { name: string; theme: string | null; sources: string[]; main: string; slides: number[] }): string {
  const ids: string[] = [];
  const inputs = o.sources.map((s) => {
    const id = freeId(slug(s, "input"), ids);
    ids.push(id);
    return { id, source: s, ...(s === o.main ? { main: true } : {}) };
  });
  const head = stringify({ spec_version: 1, name: o.name, ...(o.theme ? { theme: o.theme } : {}), output_name: `${o.name.replace(/[\\/:*?"<>|]+/g, "_")}_{period}` }, { lineWidth: 0 });
  const body = stringify({ inputs, ...(o.slides.length ? { slides: o.slides.map((id) => ({ example: id })) } : {}) }, { lineWidth: 0, indentSeq: true });
  return `# Сценарий создан в окне приложения: входы, наборы, показатели и слайды настраиваются в конструкторе.\n${head}\n${body}`;
}

function NewScenario({ onDone }: { onDone: (id: string) => void }) {
  const sources = useSources().data ?? [];
  const themes = useThemes().data ?? [];
  const [name, setName] = useState("");
  const [id, setId] = useState("");
  const [theme, setTheme] = useState<string | null>(themes.length === 1 ? themes[0].id : null);
  const [picked, setPicked] = useState<string[]>([]);
  const [main, setMain] = useState("");
  const [allSlides, setAllSlides] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const manifest = useTheme(theme ?? "").data?.current.manifest;
  const autoId = slug(name, "scenario");
  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const text = newScenarioText({
        name,
        theme,
        sources: picked,
        main: main || picked[0],
        slides: allSlides ? (manifest?.slides ?? []).map((s) => s.slide_id) : [],
      });
      const out = await unwrap(api.POST("/api/scenarios", { body: { text, id: id || autoId } }));
      onDone(out.record.id);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack>
      <TextInput label="Название" placeholder="Ежемесячный отчёт по продажам" value={name} onChange={(e) => setName(e.currentTarget.value)} withAsterisk data-autofocus />
      <TextInput label="id" description="Для команды agen run и имён запусков" placeholder={autoId} value={id} onChange={(e) => setId(e.currentTarget.value.trim())} ff="monospace" />
      <Stack gap={4}>
        <Text size="sm" fw={500}>
          Источники
        </Text>
        <Text size="xs" c="dimmed">
          Каждый источник станет входом сценария; по основному выбирается отчётный период.
        </Text>
        {!sources.length && (
          <Text size="sm" c="dimmed">
            Источников пока нет: создайте их в разделе «Источники».
          </Text>
        )}
        <Radio.Group value={main || picked[0] || ""} onChange={setMain}>
          <Stack gap={6}>
            {sources.map((s) => (
              <Group key={s.id} gap="md">
                <Checkbox
                  label={`${s.name} (${s.id})`}
                  checked={picked.includes(s.id)}
                  onChange={(e) => {
                    const on = e.currentTarget.checked;
                    setPicked((p) => (on ? [...p, s.id] : p.filter((x) => x !== s.id)));
                  }}
                  w={320}
                />
                {picked.includes(s.id) && <Radio value={s.id} label="основной" size="xs" />}
              </Group>
            ))}
          </Stack>
        </Radio.Group>
      </Stack>
      <Select
        label="Шаблон оформления"
        description="Загружается в разделе «Оформление»; можно выбрать позже"
        data={themes.map((t) => ({ value: t.id, label: `${t.name} (${t.id})` }))}
        value={theme}
        onChange={setTheme}
        clearable
      />
      {theme && !!manifest?.slides.length && (
        <Checkbox
          label={`Добавить все слайды шаблона (${manifest.slides.length}) — лишние потом выключаются или удаляются`}
          checked={allSlides}
          onChange={(e) => setAllSlides(e.currentTarget.checked)}
        />
      )}
      <ErrorAlert error={error} />
      <Group justify="flex-end">
        <Button onClick={create} loading={busy} disabled={!name.trim() || !picked.length}>
          Создать
        </Button>
      </Group>
    </Stack>
  );
}

export default function ScenariosPage() {
  const scenarios = useScenarios();
  const navigate = useNavigate();
  const [opened, modal] = useDisclosure(false);
  return (
    <Page
      title="Сценарии"
      subtitle="Обработка, наборы данных, показатели и слайды отчёта"
      actions={
        <Button leftSection={<IconPlus size={16} />} onClick={modal.open}>
          Новый сценарий
        </Button>
      }
    >
      <ErrorAlert error={scenarios.error} />
      {scenarios.data?.length === 0 && <Text c="dimmed">Сценариев пока нет.</Text>}
      {!!scenarios.data?.length && (
        <Table highlightOnHover verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Название</Table.Th>
              <Table.Th>id</Table.Th>
              <Table.Th>Версия</Table.Th>
              <Table.Th>Источники</Table.Th>
              <Table.Th>Шаблон</Table.Th>
              <Table.Th>Изменён</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {scenarios.data.map((s) => (
              <Table.Tr key={s.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/scenarios/${s.id}`)}>
                <Table.Td fw={500}>{s.name}</Table.Td>
                <Table.Td>
                  <code>{s.id}</code>
                </Table.Td>
                <Table.Td>{s.version}</Table.Td>
                <Table.Td>{s.inputs.map((i) => (i.main ? `${i.source_id} (основной)` : i.source_id)).join(", ")}</Table.Td>
                <Table.Td>{s.current.theme_id ?? "—"}</Table.Td>
                <Table.Td>{dateTime(s.current.created_at)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <Modal opened={opened} onClose={modal.close} title="Новый сценарий" size="lg">
        <NewScenario onDone={(id) => navigate(`/scenarios/${id}`)} />
      </Modal>
    </Page>
  );
}
