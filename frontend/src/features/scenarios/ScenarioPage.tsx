// Сценарий: конструктор и код правят один черновик; сохранение — новая версия.
import { Alert, Badge, Button, Group, Menu, Modal, Popover, Stack, Tabs, Text, TextInput } from "@mantine/core";
import { useDisclosure, useHotkeys } from "@mantine/hooks";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { IconDots, IconPlayerPlay } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { api, unwrap } from "../../shared/api/client";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { Issues } from "../../shared/components/Issues";
import { Page } from "../../shared/components/Page";
import { PeriodSelect } from "../../shared/components/PeriodSelect";
import { YamlEditor } from "../../shared/components/YamlEditor";
import { RunDialog } from "../runs/RunDialog";
import { RunsTable } from "../runs/RunsTable";
import { Constructor } from "./Constructor";
import { useConstructorData } from "./constructor/context";
import { isDirty, issuePath, useDraft } from "./draft";
import { useValidation } from "./Preview";
import { useScenario, useScenarioText } from "./queries";
import { Versions } from "./Versions";

function SaveButton({ scenarioId, disabled }: { scenarioId: string; disabled: boolean }) {
  const [opened, setOpened] = useState(false);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const save = useSave(scenarioId);
  const run = async () => {
    setBusy(true);
    if (await save(comment)) {
      setComment("");
      setOpened(false);
    }
    setBusy(false);
  };
  return (
    <Popover opened={opened} onChange={setOpened} position="bottom-end" withArrow trapFocus>
      <Popover.Target>
        <Button disabled={disabled} onClick={() => setOpened((o) => !o)}>
          Сохранить
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs" w={320}>
          <TextInput label="Что изменилось" placeholder="необязательно" value={comment} onChange={(e) => setComment(e.currentTarget.value)} onKeyDown={(e) => e.key === "Enter" && void run()} data-autofocus />
          <Group justify="flex-end">
            <Button size="xs" onClick={run} loading={busy}>
              Сохранить версию
            </Button>
          </Group>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

function useSave(scenarioId: string) {
  const queryClient = useQueryClient();
  return async (comment = ""): Promise<boolean> => {
    const { text, load } = useDraft.getState();
    try {
      const out = await unwrap(api.PUT("/api/scenarios/{scenario_id}/yaml", { params: { path: { scenario_id: scenarioId } }, body: { text, comment } }));
      load(scenarioId, text);
      void queryClient.invalidateQueries({ queryKey: ["scenarios"] });
      const errors = out.issues.filter((i) => i.level === "error").length;
      notifications.show({
        color: errors ? "yellow" : "teal",
        message: errors ? `Сохранена версия ${out.record.version} — черновиком: ошибок ${errors}, отчёт по ней не соберётся` : `Сохранена версия ${out.record.version}`,
      });
      return true;
    } catch (e) {
      notifications.show({ color: "red", title: "Не сохранено", message: e instanceof Error ? e.message : String(e), autoClose: 8000 });
      return false;
    }
  };
}

export default function ScenarioPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const scenario = useScenario(id);
  const saved = useScenarioText(id);
  const { scenarioId, text, base, parsed, load, setText, select } = useDraft();
  const dirty = scenarioId === id && isDirty({ text, base });
  const [tab, setTab] = useState<string | null>("constructor");
  const [period, setPeriod] = useState("");
  const [running, runModal] = useDisclosure(false);
  const save = useSave(id);

  // Сохранённый текст — в черновик, если правок нет (или открыт другой сценарий).
  useEffect(() => {
    if (saved.data === undefined) return;
    const st = useDraft.getState();
    if (st.scenarioId !== id || !isDirty(st)) {
      const restored = load(id, saved.data, st.scenarioId !== id);
      if (restored) notifications.show({ color: "yellow", title: "Несохранённые правки вернулись", message: "Они остались с прошлого раза. «Отменить правки» вернёт сохранённую версию.", autoClose: 10000 });
    }
  }, [id, saved.data, load]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  useHotkeys([["mod+S", () => dirty && void save()]], []);

  const ready = scenarioId === id;
  const spec = ready ? parsed.spec : null;
  const data = useConstructorData(spec);
  const validation = useValidation(ready ? text : "", ready && !parsed.error);
  const issues = validation.data ?? [];
  const errors = issues.filter((i) => i.level === "error").length;
  const mainInput = spec?.inputs?.find((i) => i.main) ?? spec?.inputs?.[0];

  if (scenario.error) return <ErrorAlert error={scenario.error} />;
  if (!scenario.data || !ready) return null;
  const rec = scenario.data;

  const remove = () =>
    modals.openConfirmModal({
      title: "Удалить сценарий?",
      children: <Text size="sm">Сценарий «{rec.name}», все его версии и запуски будут удалены. Готовые отчёты в выбранных папках останутся.</Text>,
      labels: { confirm: "Удалить", cancel: "Отмена" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await unwrap(api.DELETE("/api/scenarios/{scenario_id}", { params: { path: { scenario_id: id } } }));
          navigate("/scenarios");
        } catch (e) {
          notifications.show({ color: "red", title: "Сценарий не удалён", message: e instanceof Error ? e.message : String(e) });
        }
      },
    });
  const copy = () => {
    let newId = `${id}_copy`;
    modals.openConfirmModal({
      title: "Копия сценария",
      children: <TextInput label="id копии" defaultValue={newId} onChange={(e) => (newId = e.currentTarget.value.trim())} />,
      labels: { confirm: "Скопировать", cancel: "Отмена" },
      onConfirm: async () => {
        try {
          const r = await unwrap(api.POST("/api/scenarios/{scenario_id}/copy", { params: { path: { scenario_id: id } }, body: { id: newId } }));
          navigate(`/scenarios/${r.id}`);
        } catch (e) {
          notifications.show({ color: "red", title: "Не скопирован", message: e instanceof Error ? e.message : String(e) });
        }
      },
    });
  };

  return (
    <Page
      title={
        <Group gap="xs">
          {spec?.name || rec.name}
          {dirty && (
            <Badge color="orange" variant="light">
              не сохранено
            </Badge>
          )}
        </Group>
      }
      subtitle={
        <>
          <code>{rec.id}</code> · версия {rec.version}
          {rec.current.theme_id ? ` · шаблон ${rec.current.theme_id} (версия ${rec.current.theme_version})` : ""}
        </>
      }
      actions={
        <>
          {dirty && (
            <Button variant="subtle" color="gray" onClick={() => load(id, base)}>
              Отменить правки
            </Button>
          )}
          <SaveButton scenarioId={id} disabled={!dirty} />
          <Button leftSection={<IconPlayerPlay size={16} />} variant="light" onClick={runModal.open}>
            Собрать отчёт
          </Button>
          <Menu position="bottom-end">
            <Menu.Target>
              <Button variant="default" px="xs" aria-label="Ещё">
                <IconDots size={16} />
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item onClick={copy}>Копировать</Menu.Item>
              <Menu.Item color="red" onClick={remove}>
                Удалить
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </>
      }
    >
      <Tabs value={tab} onChange={setTab} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="constructor">Конструктор</Tabs.Tab>
          <Tabs.Tab
            value="code"
            rightSection={
              parsed.error ? (
                <Badge size="xs" color="red">
                  !
                </Badge>
              ) : null
            }
          >
            Код
          </Tabs.Tab>
          <Tabs.Tab value="versions">Версии</Tabs.Tab>
          <Tabs.Tab value="runs">Запуски</Tabs.Tab>
          <Group ml="auto" gap="xs" pb={4}>
            {validation.isFetching && (
              <Text size="xs" c="dimmed">
                проверяю…
              </Text>
            )}
            {!validation.isFetching && validation.data && (
              <Badge color={errors ? "red" : issues.length ? "yellow" : "teal"} variant="light">
                {errors ? `ошибок: ${errors}` : issues.length ? `замечаний: ${issues.length}` : "проверка пройдена"}
              </Badge>
            )}
            <PeriodSelect sourceId={mainInput?.source} value={period} onChange={setPeriod} label="" size="xs" w={210} />
          </Group>
        </Tabs.List>
        <Tabs.Panel value="constructor" pt="md">
          <Constructor spec={spec} data={data} issues={issues} period={period} />
        </Tabs.Panel>
        <Tabs.Panel value="code" pt="md">
          <Group align="flex-start" wrap="nowrap" gap="md">
            <div style={{ flex: "1 1 60%", border: "1px solid var(--mantine-color-default-border)", borderRadius: 4, overflow: "hidden" }}>
              <YamlEditor name={id} value={text} onChange={setText} height="calc(100vh - 240px)" />
            </div>
            <Stack style={{ flex: "0 1 38%" }} gap="sm">
              {parsed.error && <Alert color="red" title="YAML не читается">{parsed.error}</Alert>}
              <Text fw={500}>Проверка</Text>
              <Issues issues={issues} empty="Замечаний нет." />
              {issues.some((i) => issuePath(spec, i.node)) && (
                <Text size="xs" c="dimmed">
                  Место замечания открывается в конструкторе:{" "}
                  {issues
                    .map((i) => ({ i, p: issuePath(spec, i.node) }))
                    .filter((x) => x.p)
                    .slice(0, 8)
                    .map(({ i, p }, n) => (
                      <Button
                        key={n}
                        size="compact-xs"
                        variant="subtle"
                        onClick={() => {
                          select(p!);
                          setTab("constructor");
                        }}
                      >
                        {i.node}
                      </Button>
                    ))}
                </Text>
              )}
            </Stack>
          </Group>
        </Tabs.Panel>
        <Tabs.Panel value="versions" pt="md">
          <Versions scenarioId={id} />
        </Tabs.Panel>
        <Tabs.Panel value="runs" pt="md">
          <RunsTable scenario={id} />
        </Tabs.Panel>
      </Tabs>
      <Modal opened={running} onClose={runModal.close} title={`Собрать отчёт: ${rec.name}`} size="lg">
        <RunDialog scenarioId={id} mainSource={mainInput?.source ?? null} dirty={dirty} />
      </Modal>
    </Page>
  );
}
