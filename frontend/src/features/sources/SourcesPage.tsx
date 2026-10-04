import { Button, Checkbox, Group, Modal, Stack, Table, Text, TextInput } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconPlus, IconUpload } from "@tabler/icons-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { api, unwrap, type Schemas, type SourceSpec } from "../../shared/api/client";
import { runJob } from "../../shared/api/jobs";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { JobProgress } from "../../shared/components/JobProgress";
import { Page } from "../../shared/components/Page";
import { PathInput } from "../../shared/components/PathInput";
import { dateTime, fileName } from "../../shared/format";
import { ColumnsEditor } from "./ColumnsEditor";
import { useSources } from "./queries";

type Draft = Schemas["SourceDraftOut"];

function suggestId(name: string): string {
  const tr: Record<string, string> = { а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "c", ч: "ch", ш: "sh", щ: "sch", ы: "y", э: "e", ю: "yu", я: "ya" };
  const s = [...name.toLowerCase()].map((ch) => tr[ch] ?? ch).join("");
  return s.replace(/\.[a-z0-9]+$/, "").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/_?\d{4}[_-]?\d{2}$/, "").slice(0, 40) || "source";
}

function NewSource({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [path, setPath] = useState("");
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [slice, setSlice] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [spec, setSpec] = useState<SourceSpec | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);

  const read = async () => {
    setBusy(true);
    setError(null);
    try {
      const sid = id || suggestId(fileName(path));
      const out = await runJob<Draft>(
        () => unwrap(api.POST("/api/sources/draft", { body: { path, id: sid, name: name || null, period_from: slice ? "upload" : null } })),
        (j) => setJobId(j.id),
      );
      setDraft(out);
      setSpec(out.source);
      setId(out.source.id);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
      setJobId(null);
    }
  };

  const save = async () => {
    if (!spec) return;
    setBusy(true);
    setError(null);
    try {
      const rec = await unwrap(api.POST("/api/sources", { body: { spec: { ...spec, id, name: name || spec.name } } }));
      notifications.show({ color: "teal", message: `Источник «${rec.name}» создан. Первую выгрузку можно загрузить сразу.` });
      onClose();
      navigate(`/sources/${rec.id}`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack>
      {!spec ? (
        <>
          <Text size="sm" c="dimmed">
            Приложение прочитает начало файла, определит столбцы, их типы и столбец с датами. Настройки можно поправить
            перед сохранением.
          </Text>
          <PathInput label="Образец выгрузки" value={path} onChange={setPath} extensions={["csv", "xlsx", "xls", "xlsb"]} required />
          <Group grow>
            <TextInput label="Название" placeholder="Продажи из CRM" value={name} onChange={(e) => setName(e.currentTarget.value)} />
            <TextInput
              label="id"
              description="Латиницей; на него ссылаются сценарии"
              placeholder={path ? suggestId(fileName(path)) : "sales"}
              value={id}
              onChange={(e) => setId(e.currentTarget.value)}
            />
          </Group>
          <Checkbox
            label="Выгрузка — срез на дату (например, клиенты на конец месяца): период задаётся при загрузке"
            checked={slice}
            onChange={(e) => setSlice(e.currentTarget.checked)}
          />
          <JobProgress jobId={jobId} />
          <ErrorAlert error={error} />
          <Group justify="flex-end">
            <Button onClick={read} loading={busy} disabled={!path}>
              Прочитать файл
            </Button>
          </Group>
        </>
      ) : (
        <>
          <Group grow>
            <TextInput label="Название" value={name || spec.name} onChange={(e) => setName(e.currentTarget.value)} />
            <TextInput label="id" value={id} onChange={(e) => setId(e.currentTarget.value)} />
          </Group>
          {draft?.snapshot.notes.map((n, i) => (
            <Text key={i} size="sm" c="dimmed">
              {n}
            </Text>
          ))}
          <ColumnsEditor spec={spec} onChange={setSpec} snapshot={draft?.snapshot} />
          <ErrorAlert error={error} />
          <Group justify="space-between">
            <Button variant="default" onClick={() => setSpec(null)}>
              Другой файл
            </Button>
            <Button onClick={save} loading={busy}>
              Создать источник
            </Button>
          </Group>
        </>
      )}
    </Stack>
  );
}

function ImportSources({ onClose }: { onClose: () => void }) {
  const [path, setPath] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Stack>
      <PathInput label="Файл YAML с источниками" value={path} onChange={setPath} extensions={["yaml", "yml"]} />
      <Text size="sm" c="dimmed">
        Новые источники создаются, у существующих появляется новая версия настроек.
      </Text>
      <ErrorAlert error={error} />
      <Group justify="flex-end">
        <Button
          loading={busy}
          disabled={!path}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              const out = await unwrap(api.POST("/api/sources/import", { body: { path } }));
              notifications.show({ color: "teal", message: `Источников из файла: ${out.length}` });
              onClose();
            } catch (e) {
              setError(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          Импортировать
        </Button>
      </Group>
    </Stack>
  );
}

export default function SourcesPage() {
  const sources = useSources();
  const navigate = useNavigate();
  const [creating, create] = useDisclosure(false);
  const [importing, importer] = useDisclosure(false);
  return (
    <Page
      title="Источники"
      subtitle="Выгрузки одного вида: их история и настройки"
      actions={
        <>
          <Button variant="default" leftSection={<IconUpload size={16} />} onClick={importer.open}>
            Из YAML
          </Button>
          <Button leftSection={<IconPlus size={16} />} onClick={create.open}>
            Новый источник
          </Button>
        </>
      }
    >
      <ErrorAlert error={sources.error} />
      {sources.data && sources.data.length === 0 && (
        <Text c="dimmed">Источников пока нет. Создайте первый по образцу выгрузки.</Text>
      )}
      {!!sources.data?.length && (
        <Table highlightOnHover verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Название</Table.Th>
              <Table.Th>id</Table.Th>
              <Table.Th>Столбцов</Table.Th>
              <Table.Th>Версия настроек</Table.Th>
              <Table.Th>Создан</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {sources.data.map((s) => (
              <Table.Tr key={s.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/sources/${s.id}`)}>
                <Table.Td fw={500}>{s.name}</Table.Td>
                <Table.Td>
                  <code>{s.id}</code>
                </Table.Td>
                <Table.Td>{s.spec.columns.length}</Table.Td>
                <Table.Td>{s.version}</Table.Td>
                <Table.Td>{dateTime(s.created_at)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <Modal opened={creating} onClose={create.close} title="Новый источник" size="xl">
        <NewSource onClose={create.close} />
      </Modal>
      <Modal opened={importing} onClose={importer.close} title="Источники из YAML">
        <ImportSources onClose={importer.close} />
      </Modal>
    </Page>
  );
}
