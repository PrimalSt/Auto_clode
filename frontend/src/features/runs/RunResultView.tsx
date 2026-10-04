// Итог запуска: что собрано, куда, сколько заняло, замечания и журнал узлов.
import { Alert, Anchor, Badge, Button, Group, Stack, Table, Text } from "@mantine/core";
import { withToken, type RunRecord } from "../../shared/api/client";
import { Issues } from "../../shared/components/Issues";
import { count, dateTime, fileName, periodLabel } from "../../shared/format";
import { RUN_STATUS } from "../../shared/labels";
import { outputUrl } from "./queries";

export function RunStatus({ status }: { status: string }) {
  const s = RUN_STATUS[status] ?? { label: status, color: "gray" };
  return (
    <Badge color={s.color} variant="light">
      {s.label}
    </Badge>
  );
}

/** Открыть отчёт: в оболочке — PowerPoint, в браузере — скачать. */
export function OpenReport({ run, size = "xs" }: { run: RunRecord; size?: string }) {
  const path = run.output_copy ?? run.result?.output_path ?? null;
  const bridge = window.__AGEN__;
  if (!run.output_uri && !path) return null;
  if (bridge?.openPath && path) {
    return (
      <Button size={size} variant="light" onClick={() => void bridge.openPath?.(path)}>
        Открыть отчёт
      </Button>
    );
  }
  return (
    <Button size={size} variant="light" component="a" href={withToken(outputUrl(run.id))} download={path ? fileName(path) : `${run.id}.pptx`}>
      Скачать отчёт
    </Button>
  );
}

export function RunResultView({ run }: { run: RunRecord }) {
  const r = run.result;
  const seconds = r?.seconds ?? (run.finished_at ? (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000 : null);
  return (
    <Stack gap="sm">
      <Group gap="xs">
        <RunStatus status={run.status} />
        <Text size="sm">
          {run.scenario_name} · версия {run.scenario_version} · {periodLabel(run.period)}
          {run.period_given ? "" : " (последний загруженный)"}
        </Text>
      </Group>
      <Text size="sm" c="dimmed">
        Запуск {run.id} · {dateTime(run.started_at)}
        {seconds != null ? ` · ${seconds.toFixed(1)} с` : ""}
        {r ? ` · слайдов: ${r.slides}` : ""} · {run.trigger === "app" ? "из окна" : run.trigger === "cli" ? "из командной строки" : run.trigger}
      </Text>
      {(run.output_copy || r?.output_path) && (
        <Group gap="xs">
          <Text size="sm">Файл: {run.output_copy ?? r?.output_path}</Text>
          <OpenReport run={run} />
        </Group>
      )}
      {run.status === "failed" && !r?.issues.length && <Alert color="red">Отчёт не собран.</Alert>}
      {r && <Issues issues={r.issues} />}
      {r && r.nodes.length > 0 && (
        <Table fz="sm" verticalSpacing={2}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Узел</Table.Th>
              <Table.Th>Итог</Table.Th>
              <Table.Th>Строк</Table.Th>
              <Table.Th>с</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {r.nodes.map((n) => (
              <Table.Tr key={n.id}>
                <Table.Td ff="monospace">{n.id}</Table.Td>
                <Table.Td>
                  <Text size="sm" c={n.state === "error" ? "red" : n.state === "skipped" ? "dimmed" : undefined}>
                    {n.state === "ok" ? "готов" : n.state === "error" ? "ошибка" : `пропущен${n.blocked_by ? ` (из-за ${n.blocked_by})` : ""}`}
                  </Text>
                  {n.message && (
                    <Text size="xs" c="dimmed">
                      {n.message}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>{n.rows_out != null ? count(n.rows_out) : ""}</Table.Td>
                <Table.Td>{n.seconds != null ? n.seconds.toFixed(2) : ""}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {Object.keys(run.source_versions).length > 0 && (
        <Text size="xs" c="dimmed">
          Версии источников: {Object.entries(run.source_versions).map(([k, v]) => `${k} — ${v}`).join(", ")}
          {run.theme_id ? ` · шаблон ${run.theme_id}, версия ${run.theme_version}` : ""}
        </Text>
      )}
      {run.output_uri && !run.output_copy && (
        <Anchor size="xs" href={withToken(outputUrl(run.id))}>
          Отчёт хранится в папке данных
        </Anchor>
      )}
    </Stack>
  );
}
