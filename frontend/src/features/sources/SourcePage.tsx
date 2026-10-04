import { ActionIcon, Badge, Button, Card, Checkbox, Group, Menu, Stack, Table, Tabs, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { IconDots, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { api, unwrap, type SourceSpec, type UploadRecord } from "../../shared/api/client";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { Page } from "../../shared/components/Page";
import { bytes, count, dateTime, periodLabel } from "../../shared/format";
import { UPLOAD_STATUS } from "../../shared/labels";
import { UploadPanel } from "../upload-review/UploadPanel";
import { ColumnsEditor } from "./ColumnsEditor";
import { Coverage } from "./Coverage";
import { useHistory, useSource, useSourceVersions, useUsage } from "./queries";

function UploadActions({ u }: { u: UploadRecord }) {
  const [error, setError] = useState<unknown>(null);
  const patch = async (body: { status: UploadRecord["status"] }) => {
    try {
      await unwrap(api.PATCH("/api/uploads/{upload_id}", { params: { path: { upload_id: u.id } }, body }));
    } catch (e) {
      setError(e);
    }
  };
  const remove = () =>
    modals.openConfirmModal({
      title: "Удалить загрузку?",
      children: <Text size="sm">Загрузка #{u.seq} ({u.original_name}) и её данные будут удалены из истории.</Text>,
      labels: { confirm: "Удалить", cancel: "Отмена" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await unwrap(api.DELETE("/api/uploads/{upload_id}", { params: { path: { upload_id: u.id } } }));
        } catch (e) {
          setError(e);
        }
      },
    });
  return (
    <>
      <Menu position="bottom-end">
        <Menu.Target>
          <ActionIcon variant="subtle" aria-label="Действия">
            <IconDots size={16} />
          </ActionIcon>
        </Menu.Target>
        <Menu.Dropdown>
          {u.status === "needs_review" && <Menu.Item onClick={() => patch({ status: "active" })}>Принять в историю</Menu.Item>}
          {u.status === "active" && <Menu.Item onClick={() => patch({ status: "excluded" })}>Исключить из истории</Menu.Item>}
          {u.status === "excluded" && <Menu.Item onClick={() => patch({ status: "active" })}>Вернуть в историю</Menu.Item>}
          <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={remove}>
            Удалить
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
      {error ? <ErrorAlert error={error} /> : null}
    </>
  );
}

function HistoryTab({ spec }: { spec: SourceSpec }) {
  const history = useHistory(spec.id);
  const uploads = [...(history.data?.uploads ?? [])].reverse();
  return (
    <Stack>
      <Card withBorder>
        <Title order={5} mb="sm">
          Загрузить выгрузку
        </Title>
        <UploadPanel source={spec} />
      </Card>
      <ErrorAlert error={history.error} />
      {history.data?.coverage && <Coverage report={history.data.coverage} />}
      {history.data && (
        <Text size="sm" c="dimmed">
          Загрузок: {history.data.uploads.length}, на диске {bytes(history.data.disk_usage)}
        </Text>
      )}
      {uploads.length > 0 && (
        <Table verticalSpacing="xs" highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>#</Table.Th>
              <Table.Th>Файл</Table.Th>
              <Table.Th>Период</Table.Th>
              <Table.Th>Строк</Table.Th>
              <Table.Th>Статус</Table.Th>
              <Table.Th>Загружена</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {uploads.map((u) => (
              <Table.Tr key={u.id}>
                <Table.Td>{u.seq}</Table.Td>
                <Table.Td>
                  <Text size="sm">{u.original_name}</Text>
                  {u.review_reasons.map((r, i) => (
                    <Text key={i} size="xs" c="orange">
                      {r}
                    </Text>
                  ))}
                </Table.Td>
                <Table.Td>{periodLabel(u.period)}</Table.Td>
                <Table.Td>
                  {count(u.rows)}
                  {u.rows_outside_period > 0 && (
                    <Tooltip label="Строки с датами вне периода загрузки">
                      <Text size="xs" c="orange">
                        вне периода: {count(u.rows_outside_period)}
                      </Text>
                    </Tooltip>
                  )}
                </Table.Td>
                <Table.Td>
                  <Badge color={UPLOAD_STATUS[u.status]?.color} variant="light">
                    {UPLOAD_STATUS[u.status]?.label ?? u.status}
                  </Badge>
                </Table.Td>
                <Table.Td>{dateTime(u.uploaded_at)}</Table.Td>
                <Table.Td>
                  <UploadActions u={u} />
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}

function StructureTab({ spec, hasUploads }: { spec: SourceSpec; hasUploads: boolean }) {
  const [draft, setDraft] = useState<SourceSpec>(spec);
  const [comment, setComment] = useState("");
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const usage = useUsage(spec.id);
  useEffect(() => setDraft(spec), [spec]);
  const changed = JSON.stringify(draft) !== JSON.stringify(spec);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const rec = await unwrap(api.PUT("/api/sources/{source_id}", { params: { path: { source_id: spec.id } }, body: { spec: draft, comment, force } }));
      notifications.show({ color: "teal", message: `Сохранено: версия настроек ${rec.version}` });
      setComment("");
      setForce(false);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const required = usage.data?.required;
  return (
    <Stack>
      {required && required.length > 0 && (
        <Text size="sm" c="dimmed">
          Сценарии используют столбцы: {required.join(", ")}
        </Text>
      )}
      <ColumnsEditor spec={draft} onChange={setDraft} lockedIds={hasUploads} />
      <Group align="flex-end">
        <TextInput label="Комментарий к версии" value={comment} onChange={(e) => setComment(e.currentTarget.value)} style={{ flex: 1 }} />
        {hasUploads && (
          <Checkbox label="Разрешить убрать столбцы, которые есть в загрузках" checked={force} onChange={(e) => setForce(e.currentTarget.checked)} />
        )}
        <Button onClick={save} loading={busy} disabled={!changed}>
          Сохранить версию
        </Button>
      </Group>
      <ErrorAlert error={error} />
    </Stack>
  );
}

function VersionsTab({ id }: { id: string }) {
  const versions = useSourceVersions(id);
  return (
    <Stack>
      <ErrorAlert error={versions.error} />
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Версия</Table.Th>
            <Table.Th>Когда</Table.Th>
            <Table.Th>Комментарий</Table.Th>
            <Table.Th>Столбцов</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {[...(versions.data ?? [])].reverse().map((v) => (
            <Table.Tr key={v.number}>
              <Table.Td>{v.number}</Table.Td>
              <Table.Td>{dateTime(v.created_at)}</Table.Td>
              <Table.Td>{v.comment}</Table.Td>
              <Table.Td>{v.spec.columns.length}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}

export default function SourcePage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const source = useSource(id);
  const history = useHistory(id);
  const remove = () =>
    modals.openConfirmModal({
      title: "Удалить источник?",
      children: <Text size="sm">Источник «{source.data?.name}» будет удалён вместе со всеми загрузками и их файлами.</Text>,
      labels: { confirm: "Удалить", cancel: "Отмена" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await unwrap(api.DELETE("/api/sources/{source_id}", { params: { path: { source_id: id } } }));
          navigate("/sources");
        } catch (e) {
          notifications.show({ color: "red", title: "Источник не удалён", message: e instanceof Error ? e.message : String(e) });
        }
      },
    });
  if (source.error) return <ErrorAlert error={source.error} />;
  if (!source.data) return null;
  const spec = source.data.spec;
  return (
    <Page
      title={source.data.name}
      subtitle={
        <>
          <code>{spec.id}</code> · версия настроек {source.data.version}
        </>
      }
      actions={
        <Button variant="subtle" color="red" onClick={remove}>
          Удалить
        </Button>
      }
    >
      <Tabs defaultValue="history" keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="history">История</Tabs.Tab>
          <Tabs.Tab value="structure">Структура</Tabs.Tab>
          <Tabs.Tab value="versions">Версии</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="history" pt="md">
          <HistoryTab spec={spec} />
        </Tabs.Panel>
        <Tabs.Panel value="structure" pt="md">
          <StructureTab spec={spec} hasUploads={!!history.data?.uploads.length} />
        </Tabs.Panel>
        <Tabs.Panel value="versions" pt="md">
          <VersionsTab id={id} />
        </Tabs.Panel>
      </Tabs>
    </Page>
  );
}
