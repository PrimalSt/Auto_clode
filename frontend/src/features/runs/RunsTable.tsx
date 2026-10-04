// Журнал запусков: что, когда, за какой период и с каким итогом; пересборка и удаление.
import { ActionIcon, Button, Drawer, Group, Menu, Stack, Table, Text } from "@mantine/core";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { IconDots } from "@tabler/icons-react";
import { useState } from "react";
import { api, unwrap, type RunRecord } from "../../shared/api/client";
import { runJob } from "../../shared/api/jobs";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { dateTime, periodLabel } from "../../shared/format";
import { useRuns } from "./queries";
import { OpenReport, RunResultView, RunStatus } from "./RunResultView";

async function rerun(run: RunRecord, onDone: (r: RunRecord) => void) {
  notifications.show({ id: `rerun-${run.id}`, loading: true, message: `Пересборка за ${periodLabel(run.period)}…`, autoClose: false });
  try {
    const res = await runJob<RunRecord>(() => unwrap(api.POST("/api/runs/{run_id}/rerun", { params: { path: { run_id: run.id } }, body: {} })));
    notifications.update({ id: `rerun-${run.id}`, loading: false, color: res.status === "failed" ? "red" : "teal", message: `Готово: запуск ${res.id}`, autoClose: 4000 });
    onDone(res);
  } catch (e) {
    notifications.update({ id: `rerun-${run.id}`, loading: false, color: "red", title: "Не пересобран", message: e instanceof Error ? e.message : String(e), autoClose: 8000 });
  }
}

function remove(run: RunRecord) {
  modals.openConfirmModal({
    title: "Удалить запуск?",
    children: <Text size="sm">Запись запуска {run.id} и отчёт в папке данных будут удалены. Копия в выбранной папке останется.</Text>,
    labels: { confirm: "Удалить", cancel: "Отмена" },
    confirmProps: { color: "red" },
    onConfirm: async () => {
      try {
        await unwrap(api.DELETE("/api/runs/{run_id}", { params: { path: { run_id: run.id } } }));
      } catch (e) {
        notifications.show({ color: "red", title: "Запуск не удалён", message: e instanceof Error ? e.message : String(e) });
      }
    },
  });
}

export function RunsTable({ scenario }: { scenario?: string }) {
  const runs = useRuns(scenario);
  const [open, setOpen] = useState<RunRecord | null>(null);
  if (runs.error) return <ErrorAlert error={runs.error} />;
  if (runs.data && !runs.data.length) return <Text c="dimmed">Запусков пока нет.</Text>;
  return (
    <>
      <Table highlightOnHover verticalSpacing="xs">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Запуск</Table.Th>
            {!scenario && <Table.Th>Сценарий</Table.Th>}
            <Table.Th>Период</Table.Th>
            <Table.Th>Итог</Table.Th>
            <Table.Th>Когда</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(runs.data ?? []).map((r) => (
            <Table.Tr key={r.id} style={{ cursor: "pointer" }} onClick={() => setOpen(r)}>
              <Table.Td ff="monospace">{r.id}</Table.Td>
              {!scenario && <Table.Td>{r.scenario_name}</Table.Td>}
              <Table.Td>{periodLabel(r.period)}</Table.Td>
              <Table.Td>
                <RunStatus status={r.status} />
              </Table.Td>
              <Table.Td>{dateTime(r.started_at)}</Table.Td>
              <Table.Td onClick={(e) => e.stopPropagation()}>
                <Group gap={4} justify="flex-end" wrap="nowrap">
                  {r.status !== "running" && r.status !== "failed" && <OpenReport run={r} />}
                  <Menu position="bottom-end">
                    <Menu.Target>
                      <ActionIcon variant="subtle" aria-label="Действия">
                        <IconDots size={16} />
                      </ActionIcon>
                    </Menu.Target>
                    <Menu.Dropdown>
                      <Menu.Item disabled={r.status === "running"} onClick={() => void rerun(r, setOpen)}>
                        Пересобрать за этот период
                      </Menu.Item>
                      <Menu.Item color="red" disabled={r.status === "running"} onClick={() => remove(r)}>
                        Удалить
                      </Menu.Item>
                    </Menu.Dropdown>
                  </Menu>
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Drawer opened={!!open} onClose={() => setOpen(null)} position="right" size="xl" title={open ? `Запуск ${open.id}` : ""}>
        {open && (
          <Stack>
            <RunResultView run={open} />
            <Group>
              <Button variant="default" disabled={open.status === "running"} onClick={() => void rerun(open, setOpen)}>
                Пересобрать по текущей истории
              </Button>
            </Group>
          </Stack>
        )}
      </Drawer>
    </>
  );
}
