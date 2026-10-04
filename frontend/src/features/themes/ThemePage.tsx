import { Badge, Button, Card, Group, Modal, Select, SimpleGrid, Stack, Table, Tabs, Text } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { api, unwrap, type Schemas } from "../../shared/api/client";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { Issues } from "../../shared/components/Issues";
import { Page } from "../../shared/components/Page";
import { PathInput } from "../../shared/components/PathInput";
import { dateTime } from "../../shared/format";
import { LAYOUT_ROLES } from "../../shared/labels";
import { useTheme, useThemeVersions } from "./queries";
import { ImportSummary, ThemeImport } from "./ThemeImport";

type Manifest = Schemas["ThemeManifest"];

function RolesTab({ themeId, manifest }: { themeId: string; manifest: Manifest }) {
  const initial = Object.fromEntries(manifest.roles.map((r) => [r.role, r.layout_key]));
  const [roles, setRoles] = useState<Record<string, string>>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [out, setOut] = useState<Schemas["ThemeImportOut"] | null>(null);
  useEffect(() => setRoles(Object.fromEntries(manifest.roles.map((r) => [r.role, r.layout_key]))), [manifest]);
  const layouts = manifest.layouts.map((l) => ({ value: l.key, label: `${l.name}${l.slides ? ` (слайдов: ${l.slides})` : ""}` }));
  const confirm = async (only?: string) => {
    setBusy(true);
    setError(null);
    try {
      const body = Object.fromEntries(
        manifest.roles.filter((r) => !only || r.role === only).map((r) => [r.role, roles[r.role] ?? r.layout_key]),
      );
      const res = await unwrap(api.PUT("/api/themes/{theme_id}/roles", { params: { path: { theme_id: themeId } }, body: { roles: body } }));
      setOut(res);
      if (!res.skipped) notifications.show({ color: "teal", message: `Роли подтверждены: версия шаблона ${res.record.version}` });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack>
      <Text size="sm" c="dimmed">
        Роль говорит, на каком макете строить слайд сценария (например, «заголовок и объект»). Приложение подбирает
        роли само; подтвердите их или выберите другой макет.
      </Text>
      <Table verticalSpacing="sm">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Роль</Table.Th>
            <Table.Th>Макет</Table.Th>
            <Table.Th>Состояние</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {manifest.roles.map((r) => (
            <Table.Tr key={r.role}>
              <Table.Td>
                <Text fw={500}>{LAYOUT_ROLES[r.role] ?? r.role}</Text>
                <Text size="xs" c="dimmed">
                  {r.role}
                </Text>
              </Table.Td>
              <Table.Td>
                <Select data={layouts} value={roles[r.role] ?? r.layout_key} onChange={(v) => v && setRoles((s) => ({ ...s, [r.role]: v }))} allowDeselect={false} />
                {r.derived && (
                  <Text size="xs" c="dimmed" mt={2}>
                    {r.derived}
                  </Text>
                )}
                {r.decorations.length > 0 && (
                  <Text size="xs" c="dimmed" mt={2}>
                    Элементы оформления: {r.decorations.map((d) => d.name).join(", ")}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>
                {r.guessed ? (
                  <Badge color="yellow" variant="light">
                    предложено
                  </Badge>
                ) : (
                  <Badge color="teal" variant="light">
                    подтверждено
                  </Badge>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <ErrorAlert error={error} />
      {out && <ImportSummary out={out} />}
      <Group justify="flex-end">
        <Button onClick={() => confirm()} loading={busy}>
          Подтвердить роли
        </Button>
      </Group>
    </Stack>
  );
}

function SlidesTab({ manifest }: { manifest: Manifest }) {
  if (!manifest.slides.length) return <Text c="dimmed">В шаблоне нет слайдов-образцов: слайды строятся из макетов.</Text>;
  return (
    <SimpleGrid cols={{ base: 1, md: 2 }}>
      {manifest.slides.map((s) => {
        const markers = [...new Set(s.markers.map((m) => m.name))];
        return (
          <Card key={s.slide_id} withBorder>
            <Group justify="space-between" mb={6}>
              <Text fw={500}>
                Слайд {s.number}
                {s.title ? `: ${s.title}` : ""}
              </Text>
              <Badge variant="light">{s.layout_name || s.layout_key}</Badge>
            </Group>
            {markers.length > 0 && (
              <Text size="sm">
                <Text span c="dimmed">
                  Метки:{" "}
                </Text>
                {markers.map((m) => `{{${m}}}`).join(", ")}
              </Text>
            )}
            {s.charts.map((c) => (
              <Text key={c.shape_id} size="sm">
                <Text span c="dimmed">
                  График «{c.shape_name}»:{" "}
                </Text>
                {c.chart_type}, групп серий {c.groups.length}, категорий {c.categories}
              </Text>
            ))}
            {s.tables.map((t) => (
              <Text key={t.shape_id} size="sm">
                <Text span c="dimmed">
                  Таблица «{t.shape_name}»:{" "}
                </Text>
                {t.rows} × {t.cols}
                {t.header.length ? ` (${t.header.join(" | ")})` : ""}
              </Text>
            ))}
          </Card>
        );
      })}
    </SimpleGrid>
  );
}

function CheckTab({ manifest }: { manifest: Manifest }) {
  const issues = manifest.lint.map((l) => ({ level: l.level, message: l.message, node: l.slide != null ? `слайд ${l.slide}` : null, code: l.code }));
  return (
    <Stack>
      <Issues issues={issues} empty="Замечаний к шаблону нет." />
      {manifest.notes.map((n, i) => (
        <Text key={i} size="sm" c="dimmed">
          {n}
        </Text>
      ))}
      <Text size="sm" c="dimmed">
        Макетов: {manifest.layouts.length}, слайдов-образцов: {manifest.slides.length}, шрифтов: {manifest.fonts.length}
      </Text>
    </Stack>
  );
}

function VersionsTab({ themeId }: { themeId: string }) {
  const versions = useThemeVersions(themeId);
  return (
    <Table>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Версия</Table.Th>
          <Table.Th>Когда</Table.Th>
          <Table.Th>Файл</Table.Th>
          <Table.Th>Комментарий</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {[...(versions.data ?? [])].reverse().map((v) => (
          <Table.Tr key={v.number}>
            <Table.Td>{v.number}</Table.Td>
            <Table.Td>{dateTime(v.imported_at)}</Table.Td>
            <Table.Td>{v.original_name}</Table.Td>
            <Table.Td>{v.comment}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function ExportForm({ themeId, onDone }: { themeId: string; onDone: () => void }) {
  const [out, setOut] = useState("");
  const [error, setError] = useState<unknown>(null);
  return (
    <Stack>
      <PathInput label="Куда сохранить" folder value={out} onChange={setOut} description="Папка или файл .pptx" />
      <ErrorAlert error={error} />
      <Group justify="flex-end">
        <Button
          disabled={!out}
          onClick={async () => {
            try {
              const res = await unwrap(api.POST("/api/themes/{theme_id}/export", { params: { path: { theme_id: themeId } }, body: { out } }));
              notifications.show({ color: "teal", message: `Сохранено: ${res.path}` });
              onDone();
            } catch (e) {
              setError(e);
            }
          }}
        >
          Сохранить
        </Button>
      </Group>
    </Stack>
  );
}

export default function ThemePage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const theme = useTheme(id);
  const [reimport, reimportModal] = useDisclosure(false);
  const [exporting, exportModal] = useDisclosure(false);
  if (theme.error) return <ErrorAlert error={theme.error} />;
  if (!theme.data) return null;
  const t = theme.data;
  const remove = () =>
    modals.openConfirmModal({
      title: "Удалить шаблон?",
      children: <Text size="sm">Шаблон «{t.name}» и все его версии будут удалены. Шаблон, на котором стоят сценарии, не удаляется.</Text>,
      labels: { confirm: "Удалить", cancel: "Отмена" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await unwrap(api.DELETE("/api/themes/{theme_id}", { params: { path: { theme_id: id } } }));
          navigate("/themes");
        } catch (e) {
          notifications.show({ color: "red", title: "Шаблон не удалён", message: e instanceof Error ? e.message : String(e) });
        }
      },
    });
  return (
    <Page
      title={t.name}
      subtitle={
        <>
          <code>{t.id}</code> · версия {t.version} · {t.current.original_name}
        </>
      }
      actions={
        <>
          <Button variant="default" onClick={reimportModal.open}>
            Новая версия
          </Button>
          <Button variant="default" onClick={exportModal.open}>
            Сохранить файл
          </Button>
          <Button variant="subtle" color="red" onClick={remove}>
            Удалить
          </Button>
        </>
      }
    >
      <Tabs defaultValue="roles" keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="roles">Роли макетов</Tabs.Tab>
          <Tabs.Tab value="slides">Слайды-образцы</Tabs.Tab>
          <Tabs.Tab value="check">Проверка</Tabs.Tab>
          <Tabs.Tab value="versions">Версии</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="roles" pt="md">
          <RolesTab themeId={id} manifest={t.current.manifest} />
        </Tabs.Panel>
        <Tabs.Panel value="slides" pt="md">
          <SlidesTab manifest={t.current.manifest} />
        </Tabs.Panel>
        <Tabs.Panel value="check" pt="md">
          <CheckTab manifest={t.current.manifest} />
        </Tabs.Panel>
        <Tabs.Panel value="versions" pt="md">
          <VersionsTab themeId={id} />
        </Tabs.Panel>
      </Tabs>
      <Modal opened={reimport} onClose={reimportModal.close} title="Новая версия шаблона" size="lg">
        <Text size="sm" c="dimmed" mb="sm">
          Доработанный в PowerPoint файл: роли и привязки переносятся, сценарии переходят на новую версию, если на ней всё
          сходится.
        </Text>
        <ThemeImport themeId={id} />
      </Modal>
      <Modal opened={exporting} onClose={exportModal.close} title="Сохранить файл шаблона">
        <ExportForm themeId={id} onDone={exportModal.close} />
      </Modal>
    </Page>
  );
}
