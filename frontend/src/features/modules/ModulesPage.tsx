// Модули: исполнители, модули и плагины с их состоянием, кэш, резервные копии.
import { Alert, Badge, Button, Card, Code, Group, SimpleGrid, Stack, Table, Tabs, Text, Tooltip } from "@mantine/core";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "../../shared/api/client";
import { useModules, type PluginInfo } from "../../shared/api/modules";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { Page } from "../../shared/components/Page";
import { bytes, dateTime } from "../../shared/format";

const KINDS: Record<string, string> = {
  reader: "Чтение файлов",
  step: "Шаги обработки",
  window: "Окна данных",
  aggregation: "Агрегаты",
  block: "Блоки слайдов",
};

function fail(title: string, e: unknown) {
  notifications.show({ color: "red", title, message: e instanceof Error ? e.message : String(e), autoClose: 8000 });
}

function Plugins({ plugins }: { plugins: PluginInfo[] }) {
  const kinds = Object.keys(KINDS).filter((k) => plugins.some((p) => p.kind === k));
  return (
    <Tabs defaultValue={kinds[0]}>
      <Tabs.List>
        {kinds.map((k) => {
          const broken = plugins.filter((p) => p.kind === k && p.status !== "ok").length;
          return (
            <Tabs.Tab key={k} value={k} rightSection={broken ? <Badge size="xs" color="red">{broken}</Badge> : null}>
              {KINDS[k]}
            </Tabs.Tab>
          );
        })}
      </Tabs.List>
      {kinds.map((k) => (
        <Tabs.Panel key={k} value={k} pt="sm">
          <Table verticalSpacing={6}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Модуль</Table.Th>
                <Table.Th>Имя в сценарии</Table.Th>
                <Table.Th>Пакет</Table.Th>
                <Table.Th>Состояние</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {plugins
                .filter((p) => p.kind === k)
                .map((p) => (
                  <Table.Tr key={p.name}>
                    <Table.Td>{p.title}</Table.Td>
                    <Table.Td ff="monospace">{p.name}</Table.Td>
                    <Table.Td>
                      {p.distribution ?? "—"}
                      {p.version ? ` ${p.version}` : ""}
                    </Table.Td>
                    <Table.Td>
                      {p.status === "ok" ? (
                        <Badge color="teal" variant="light">
                          работает
                        </Badge>
                      ) : (
                        <Stack gap={2}>
                          <Badge color="red" variant="light">
                            ошибка
                          </Badge>
                          <Code block style={{ maxWidth: 520, whiteSpace: "pre-wrap" }}>
                            {p.error}
                          </Code>
                        </Stack>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
            </Table.Tbody>
          </Table>
        </Tabs.Panel>
      ))}
    </Tabs>
  );
}

function Backups() {
  const queryClient = useQueryClient();
  const backups = useQuery({ queryKey: ["system", "backups"], queryFn: () => unwrap(api.GET("/api/system/backups")) });
  const create = async () => {
    try {
      const b = await unwrap(api.POST("/api/system/backup", { body: { label: "manual" } }));
      notifications.show({ color: "teal", message: `Резервная копия: ${b.name}` });
      void queryClient.invalidateQueries({ queryKey: ["system", "backups"] });
    } catch (e) {
      fail("Копия не сделана", e);
    }
  };
  const restore = (name: string) =>
    modals.openConfirmModal({
      title: "Восстановить из копии?",
      children: (
        <Text size="sm">
          Метаданные папки данных (источники, сценарии, шаблоны, запуски) вернутся к состоянию копии {name}. Текущее
          состояние перед этим сохранится отдельной копией.
        </Text>
      ),
      labels: { confirm: "Восстановить", cancel: "Отмена" },
      confirmProps: { color: "orange" },
      onConfirm: async () => {
        try {
          await unwrap(api.POST("/api/system/restore", { body: { path: name } }));
          notifications.show({ color: "teal", message: "Восстановлено" });
          void queryClient.invalidateQueries();
        } catch (e) {
          fail("Не восстановлено", e);
        }
      },
    });
  return (
    <Stack>
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          Копии метаданных папки данных: база, настройки источников, сценарии и шаблоны. Сами загрузки не копируются.
        </Text>
        <Button size="xs" variant="light" onClick={create}>
          Сделать копию
        </Button>
      </Group>
      <ErrorAlert error={backups.error} />
      {backups.data?.length === 0 && <Text size="sm" c="dimmed">Копий пока нет.</Text>}
      {!!backups.data?.length && (
        <Table verticalSpacing={4}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Файл</Table.Th>
              <Table.Th>Когда</Table.Th>
              <Table.Th>Размер</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {backups.data.map((b) => (
              <Table.Tr key={b.name}>
                <Table.Td ff="monospace">{b.name}</Table.Td>
                <Table.Td>{dateTime(b.created)}</Table.Td>
                <Table.Td>{bytes(b.size)}</Table.Td>
                <Table.Td>
                  <Button size="compact-xs" variant="subtle" onClick={() => restore(b.name)}>
                    Восстановить
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}

export default function ModulesPage() {
  const queryClient = useQueryClient();
  const modules = useModules();
  const system = useQuery({ queryKey: ["system"], queryFn: () => unwrap(api.GET("/api/system")) });
  const restart = () =>
    modals.openConfirmModal({
      title: "Перезапустить исполнители?",
      children: <Text size="sm">Процессы, которые считают превью и собирают отчёты, запустятся заново и подхватят изменённый код модулей. Нужны, когда нет заданий.</Text>,
      labels: { confirm: "Перезапустить", cancel: "Отмена" },
      onConfirm: async () => {
        try {
          const out = await unwrap(api.POST("/api/modules/restart"));
          queryClient.setQueryData(["modules"], out);
          void queryClient.invalidateQueries({ queryKey: ["modules"] });
          notifications.show({ color: "teal", message: "Исполнители перезапущены" });
        } catch (e) {
          fail("Не перезапущены", e);
        }
      },
    });
  const clearCache = async () => {
    try {
      const out = await unwrap(api.POST("/api/modules/cache/clear"));
      notifications.show({ color: "teal", message: `Кэш очищен: ${bytes((out as Record<string, number>).freed ?? 0)}` });
    } catch (e) {
      fail("Кэш не очищен", e);
    }
  };
  const sys = system.data;
  return (
    <Page
      title="Модули"
      subtitle="Модули и плагины, процессы-исполнители, кэш вычислений и резервные копии"
      actions={
        <>
          <Button variant="default" onClick={clearCache}>
            Очистить кэш
          </Button>
          <Button variant="default" onClick={restart}>
            Перезапустить исполнители
          </Button>
        </>
      }
    >
      <ErrorAlert error={modules.error ?? system.error} />
      {sys && (
        <SimpleGrid cols={{ base: 1, md: 3 }}>
          <Card withBorder padding="sm">
            <Text size="xs" c="dimmed">
              Приложение
            </Text>
            <Text fw={500}>{sys.version}</Text>
            <Text size="xs" c="dimmed">
              сервер запущен {dateTime(sys.started_at)}, процесс {sys.pid}
            </Text>
          </Card>
          <Card withBorder padding="sm">
            <Text size="xs" c="dimmed">
              Папка данных
            </Text>
            <Text fw={500} style={{ wordBreak: "break-all" }}>
              {sys.home}
            </Text>
            <Text size="xs" c="dimmed">
              свободно {bytes(sys.disk_free)}
            </Text>
          </Card>
          <Card withBorder padding="sm">
            <Text size="xs" c="dimmed">
              Задания
            </Text>
            <Text fw={500}>{sys.jobs_active ? `идут: ${sys.jobs_active}` : "нет"}</Text>
          </Card>
        </SimpleGrid>
      )}
      {sys?.warnings.map((w, i) => (
        <Alert key={i} color="yellow">
          {w}
        </Alert>
      ))}
      <Card withBorder padding="sm">
        <Text fw={500} mb="xs">
          Исполнители
        </Text>
        <Table verticalSpacing={4}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Процесс</Table.Th>
              <Table.Th>Состояние</Table.Th>
              <Table.Th>Вызовов</Table.Th>
              <Table.Th>Перезапусков</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(modules.data?.executors ?? []).map((ex) => (
              <Table.Tr key={ex.name}>
                <Table.Td>
                  <Text size="sm" fw={500}>
                    {ex.name === "main" ? "основной" : ex.name === "light" ? "превью" : ex.name}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {ex.name === "main" ? "загрузки, отчёты, шаблоны" : "проверка, превью, мелкие вызовы"}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {ex.error ? (
                    <Tooltip label={<Code block>{ex.error}</Code>} multiline w={520}>
                      <Badge color="red" variant="light">
                        ошибка
                      </Badge>
                    </Tooltip>
                  ) : ex.alive ? (
                    <Badge color="teal" variant="light">
                      работает{ex.pid ? `, процесс ${ex.pid}` : ""}
                    </Badge>
                  ) : (
                    <Badge color="gray" variant="light">
                      запустится при первом вызове
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>{ex.calls}</Table.Td>
                <Table.Td>{ex.restarts}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>
      {modules.data?.error && (
        <Alert color="red" title="Манифест модулей не получен">
          {String(modules.data.error.message ?? "")}
          {modules.data.error.hint ? ` ${String(modules.data.error.hint)}` : ""}
        </Alert>
      )}
      {modules.data?.plugins && (
        <Card withBorder padding="sm">
          <Group justify="space-between" mb="xs">
            <Text fw={500}>Модули и плагины</Text>
            <Text size="xs" c="dimmed">
              API плагинов {modules.data.plugins.api_version}
            </Text>
          </Group>
          <Plugins plugins={modules.data.plugins.plugins} />
        </Card>
      )}
      <Card withBorder padding="sm">
        <Text fw={500} mb="xs">
          Резервные копии
        </Text>
        <Backups />
      </Card>
    </Page>
  );
}
