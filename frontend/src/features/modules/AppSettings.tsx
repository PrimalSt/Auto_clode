// Приложение на этом компьютере (окно оболочки): папка данных, режим разработчика, журналы.
// Проверка изменённых модулей работает и в браузере, если сервер запущен с `agen serve --dev`.
import { Alert, Badge, Button, Card, Code, Group, Stack, Table, Text } from "@mantine/core";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Schemas } from "../../shared/api/client";
import { runJob } from "../../shared/api/jobs";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { JobProgress } from "../../shared/components/JobProgress";
import { bytes } from "../../shared/format";

type SystemOut = Schemas["SystemOut"];
type CheckOut = Schemas["ModulesCheckOut"];

function fail(title: string, e: unknown) {
  notifications.show({ color: "red", title, message: e instanceof Error ? e.message : String(e), autoClose: 10000 });
}

/** Спросить и перезапустить сервер (новые настройки запуска или новый код модулей сервера). */
function confirmRestart(sys: SystemOut, title: string, text: string, action: () => Promise<void> | undefined) {
  modals.openConfirmModal({
    title,
    children: (
      <Stack gap="xs">
        <Text size="sm">{text}</Text>
        {!!sys.jobs_active && (
          <Text size="sm" c="orange">
            Идут задания ({sys.jobs_active}): они прервутся.
          </Text>
        )}
      </Stack>
    ),
    labels: { confirm: "Перезапустить", cancel: "Отмена" },
    onConfirm: async () => {
      try {
        await action();
      } catch (e) {
        fail("Не перезапущено", e);
      }
    },
  });
}

function DataFolder({ sys }: { sys: SystemOut }) {
  const bridge = window.__AGEN__;
  const settings = useQuery({ queryKey: ["shell"], queryFn: () => bridge!.settings!(), enabled: !!bridge?.settings });
  const change = async () => {
    const path = await bridge?.pickFolder?.({ title: "Папка данных Autogenerator" });
    if (!path) return;
    confirmRestart(
      sys,
      "Сменить папку данных?",
      `Приложение перезапустится с папкой ${path}. В пустой папке появится новая папка данных; данные из текущей папки не переносятся (для переноса — резервная копия ниже).`,
      () => bridge?.setHome?.(path),
    );
  };
  const reset = () =>
    confirmRestart(sys, "Вернуть папку по умолчанию?", `Приложение перезапустится с папкой ${settings.data?.default_home}.`, () =>
      bridge?.setHome?.(null),
    );
  return (
    <Stack gap={6}>
      <Text size="sm" fw={500}>
        Папка данных
      </Text>
      <Text size="sm" ff="monospace" style={{ wordBreak: "break-all" }}>
        {sys.home}
      </Text>
      <Group gap="xs">
        <Button size="xs" variant="default" onClick={() => void bridge?.openPath?.(sys.home)}>
          Открыть
        </Button>
        <Button size="xs" variant="default" onClick={change}>
          Сменить…
        </Button>
        {settings.data?.custom_home && (
          <Button size="xs" variant="subtle" onClick={reset}>
            По умолчанию
          </Button>
        )}
        <Button size="xs" variant="subtle" onClick={() => void bridge?.openLogs?.()}>
          Журналы
        </Button>
      </Group>
      {!!settings.data?.memory_limit && (
        <Text size="xs" c="dimmed">
          Предел памяти одного процесса: {bytes(settings.data.memory_limit)} (расчёт, которому не хватит памяти, остановится с ошибкой, а
          компьютер не зависнет)
        </Text>
      )}
    </Stack>
  );
}

function CheckResult({ out, sys }: { out: CheckOut; sys: SystemOut }) {
  const bridge = window.__AGEN__;
  if (!out.modules.length) return <Text size="sm">Изменённых модулей нет: с последнего применения код не менялся.</Text>;
  return (
    <Stack gap="xs">
      <Table verticalSpacing={4}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Модуль</Table.Th>
            <Table.Th>Тесты</Table.Th>
            <Table.Th>Время</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {out.modules.map((m) => (
            <Table.Tr key={m.module}>
              <Table.Td ff="monospace">{m.module}</Table.Td>
              <Table.Td>
                {m.ok ? (
                  <Badge color="teal" variant="light">
                    {m.output === "тестов нет" ? "тестов нет" : "прошли"}
                  </Badge>
                ) : (
                  <Stack gap={4}>
                    <Badge color="red" variant="light">
                      не прошли
                    </Badge>
                    <Code block style={{ maxHeight: 240, overflow: "auto", whiteSpace: "pre-wrap" }}>
                      {m.output}
                    </Code>
                  </Stack>
                )}
              </Table.Td>
              <Table.Td>{m.seconds.toFixed(1)} с</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {out.applied && <Alert color="teal">Новый код применён: исполнители перезапущены и считают с ним.</Alert>}
      {!out.applied && !out.note && out.modules.some((m) => !m.ok) && (
        <Alert color="red">Тесты не прошли: приложение работает со старым кодом. Исправьте модуль и проверьте снова.</Alert>
      )}
      {out.note && <Alert color="yellow">{out.note}</Alert>}
      {out.restart_app && (
        <Alert color="blue" title="Нужен перезапуск приложения">
          <Stack gap="xs" align="flex-start">
            <Text size="sm">Изменились модули, которые работают в самом сервере: их новый код заработает после перезапуска.</Text>
            {bridge?.restart ? (
              <Button
                size="xs"
                onClick={() =>
                  confirmRestart(sys, "Перезапустить приложение?", "Сервер приложения запустится заново с новым кодом.", () => bridge.restart?.())
                }
              >
                Перезапустить приложение
              </Button>
            ) : (
              <Text size="sm">Остановите `agen serve --dev` и запустите снова.</Text>
            )}
          </Stack>
        </Alert>
      )}
    </Stack>
  );
}

function DevMode({ sys }: { sys: SystemOut }) {
  const bridge = window.__AGEN__;
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["shell"], queryFn: () => bridge!.settings!(), enabled: !!bridge?.settings });
  const [jobId, setJobId] = useState<string | null>(null);
  const [out, setOut] = useState<CheckOut | null>(null);
  const [error, setError] = useState<unknown>(null);
  const source = settings.data?.dev_source ?? sys.dev_source;
  // `agen serve --dev` у установленного приложения: режим разработчика есть, а копии исходников нет
  const canCheck = sys.dev && !!sys.dev_source;
  const enable = async () => {
    const path = await bridge?.pickFolder?.({ title: "Копия исходников Autogenerator" });
    if (!path) return;
    confirmRestart(
      sys,
      "Включить режим разработчика?",
      `Приложение перезапустится и будет работать с кодом из ${path} (окружение .venv в этой папке, после uv sync).`,
      () => bridge?.setDevSource?.(path),
    );
  };
  const disable = () =>
    confirmRestart(sys, "Выключить режим разработчика?", "Приложение перезапустится с кодом установленной версии.", () =>
      bridge?.setDevSource?.(null),
    );
  const check = async () => {
    setOut(null);
    setError(null);
    try {
      const res = await runJob<CheckOut>(
        () => unwrap(api.POST("/api/modules/check", { body: {} })),
        (j) => setJobId(j.id),
      );
      setOut(res);
      if (res.applied) void queryClient.invalidateQueries({ queryKey: ["modules"] });
    } catch (e) {
      setError(e);
    } finally {
      setJobId(null);
    }
  };
  return (
    <Stack gap={6}>
      <Group gap="xs">
        <Text size="sm" fw={500}>
          Режим разработчика
        </Text>
        <Badge size="sm" variant="light" color={sys.dev ? "violet" : "gray"}>
          {sys.dev ? "включён" : "выключен"}
        </Badge>
      </Group>
      {canCheck ? (
        <Text size="sm">
          Код модулей — из <Code>{source}</Code>. Поправьте модуль и нажмите «Проверить и применить»: сначала пройдут его тесты, и только
          потом приложение начнёт считать с новым кодом. Если тесты не прошли, всё работает по-старому.
        </Text>
      ) : sys.dev ? (
        <Text size="sm" c="dimmed">
          Сервер запущен в режиме разработчика, но не из копии исходников, поэтому проверять и применять изменённые модули не из чего.
          Запустите <Code>uv run agen serve --dev</Code> в копии исходников.
        </Text>
      ) : (
        <Text size="sm" c="dimmed">
          Для правки модулей: приложение запускается из копии исходников (папка с pyproject.toml, после <Code>uv sync</Code>), а
          изменённые модули проверяются тестами перед применением.
        </Text>
      )}
      <Group gap="xs">
        {canCheck && (
          <Button size="xs" onClick={check} loading={!!jobId}>
            Проверить и применить
          </Button>
        )}
        {bridge?.setDevSource && !settings.data?.dev_source && (
          <Button size="xs" variant="default" onClick={enable}>
            Включить…
          </Button>
        )}
        {bridge?.setDevSource && settings.data?.dev_source && (
          <Button size="xs" variant="default" onClick={disable}>
            Выключить
          </Button>
        )}
        {bridge?.restart && (
          <Button size="xs" variant="subtle" onClick={() => confirmRestart(sys, "Перезапустить приложение?", "Сервер приложения запустится заново.", () => bridge.restart?.())}>
            Перезапустить приложение
          </Button>
        )}
      </Group>
      <JobProgress jobId={jobId} />
      <ErrorAlert error={error} />
      {out && <CheckResult out={out} sys={sys} />}
    </Stack>
  );
}

/** Карточка «Приложение на этом компьютере»: в окне оболочки или при сервере в режиме разработчика. */
export function AppSettings({ sys }: { sys: SystemOut }) {
  const shell = !!window.__AGEN__?.shell;
  if (!shell && !sys.dev) return null;
  return (
    <Card withBorder padding="sm">
      <Text fw={500} mb="xs">
        Приложение на этом компьютере
      </Text>
      <Stack gap="md">
        {shell && <DataFolder sys={sys} />}
        <DevMode sys={sys} />
      </Stack>
    </Card>
  );
}
