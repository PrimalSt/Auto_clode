// Превью выбранного места сценария на данных из истории: строки узла, число показателя,
// картинка слайда. Строится по несохранённому черновику; устаревшее превью отменяется.
import { Alert, Anchor, Badge, Center, Group, Image, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, api, unwrap, withToken, type Issue, type JobInfo, type PreviewResult, type Schemas } from "../../shared/api/client";
import { cancelJob, jobError, useJobs, waitJob } from "../../shared/api/jobs";
import { DataTable } from "../../shared/components/DataTable";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { Issues } from "../../shared/components/Issues";
import { JobProgress } from "../../shared/components/JobProgress";
import { count, periodLabel } from "../../shared/format";
import { usePreviewColumns, usePreviewSteps } from "./constructor/context";

/** Задание превью: ставится через паузу после правки, прежнее отменяется. */
function usePreviewJob<T>(key: string | null, submit: () => Promise<JobInfo>, delay = 700) {
  const [state, setState] = useState<{ resultKey: string | null; jobId: string | null; result: T | null; error: unknown; loading: boolean }>({
    resultKey: null,
    jobId: null,
    result: null,
    error: null,
    loading: false,
  });
  useEffect(() => {
    if (!key) return;
    let stale = false;
    let jobId: string | null = null;
    const timer = window.setTimeout(async () => {
      setState((s) => ({ ...s, loading: true, error: null }));
      try {
        const started = await submit();
        jobId = started.id;
        useJobs.getState().put(started);
        if (!stale) setState((s) => ({ ...s, jobId: started.id }));
        const job = await waitJob(started.id);
        if (stale) return;
        if (job.status !== "done") throw jobError(job);
        setState({ resultKey: key, jobId: started.id, result: job.result as T, error: null, loading: false });
      } catch (e) {
        if (!stale) setState((s) => ({ ...s, error: e, loading: false }));
      }
    }, delay);
    return () => {
      stale = true;
      window.clearTimeout(timer);
      if (jobId) void cancelJob(jobId).catch(() => undefined);
    };
  }, [key]); // submit меняется вместе с key
  return state;
}

/** Проверка черновика без данных (ссылки, типы, модули, метки шаблона): через паузу после правки. */
export function useValidation(text: string, enabled = true) {
  const [debounced, setDebounced] = useState(text);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(text), 600);
    return () => window.clearTimeout(t);
  }, [text]);
  return useQuery({
    queryKey: ["validate", debounced],
    queryFn: () => unwrap(api.POST("/api/preview/validate", { body: { text: debounced } })),
    enabled: enabled && !!debounced.trim(),
    placeholderData: (prev) => prev,
    staleTime: Infinity,
  });
}

function StepsTable({ steps }: { steps: Schemas["StepStat"][] }) {
  if (!steps.length) return null;
  return (
    <Table verticalSpacing={2} fz="sm">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Шаг</Table.Th>
          <Table.Th>Было</Table.Th>
          <Table.Th>Стало</Table.Th>
          <Table.Th>с</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {steps.map((s) => (
          <Table.Tr key={s.id} c={s.enabled ? undefined : "dimmed"}>
            <Table.Td>
              <Text size="sm" ff="monospace">
                {s.id}
              </Text>
              {s.error && (
                <Text size="xs" c="red">
                  {s.error}
                </Text>
              )}
            </Table.Td>
            <Table.Td>{count(s.rows_before)}</Table.Td>
            <Table.Td>{count(s.rows_after)}</Table.Td>
            <Table.Td>{s.seconds != null ? s.seconds.toFixed(2) : ""}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function formatNumber(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toLocaleString("ru-RU", { maximumFractionDigits: Math.abs(v) < 10 ? 4 : 2 });
}

export function NodePreview({ text, target, period }: { text: string; target: string; period: string }) {
  const key = JSON.stringify({ text, target, period });
  const job = usePreviewJob<PreviewResult>(key, () =>
    unwrap(api.POST("/api/preview/node", { body: { text, target, period: period || null, rows: 50 } })),
  );
  // прежний итог того же узла виден, пока считается новый
  const res = job.result && job.result.target === target ? job.result : null;
  const putColumns = usePreviewColumns((s) => s.put);
  const putSteps = usePreviewSteps((s) => s.put);
  useEffect(() => {
    if (!res) return;
    if (res.columns.length) putColumns(res.target, res.columns.map((c) => c.name));
    if (res.target.startsWith("input:") && res.steps.length) putSteps(res.target.slice(6).split("/")[0], res.steps);
  }, [res, putColumns, putSteps]);
  const errors = res?.issues.filter((i) => i.level === "error") ?? [];
  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Group gap="xs">
          <Title order={5}>Превью</Title>
          <Text size="sm" c="dimmed" ff="monospace">
            {target}
          </Text>
        </Group>
        <Group gap="xs">
          {job.loading && <Loader size="xs" />}
          {res?.approximate && (
            <Badge color="orange" variant="light" title={res.sample ? `Выборка: примерно каждый ${res.sample.k}-й ключ` : undefined}>
              ≈ по выборке
            </Badge>
          )}
          {res && <Badge variant="light">{periodLabel(res.period)}</Badge>}
        </Group>
      </Group>
      {job.loading && <JobProgress jobId={job.jobId} cancellable={false} />}
      {job.error != null && !(job.error instanceof ApiError && job.error.code === "cancelled") && <ErrorAlert error={job.error} />}
      {res && (
        <>
          {errors.length > 0 && <Issues issues={errors} />}
          {res.steps.length > 0 && <StepsTable steps={res.steps} />}
          {res.value !== null && res.value !== undefined && (
            <Stack gap={2}>
              <Text size="xs" c="dimmed">
                Значение
              </Text>
              <Text fz={28} fw={600}>
                {formatNumber(res.value)}
              </Text>
            </Stack>
          )}
          {Object.keys(res.metrics).length > 1 && (
            <Table fz="sm" verticalSpacing={2}>
              <Table.Tbody>
                {Object.entries(res.metrics).map(([k, v]) => (
                  <Table.Tr key={k}>
                    <Table.Td ff="monospace">{k}</Table.Td>
                    <Table.Td>{formatNumber(v)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
          {res.columns.length > 0 && (
            <>
              <Text size="xs" c="dimmed">
                {res.total_rows != null ? `Строк: ${res.approximate ? "≈" : ""}${count(res.total_rows)}` : ""}
                {res.rows.length < (res.total_rows ?? 0) ? `, показаны первые ${res.rows.length}` : ""} · {res.seconds.toFixed(1)} с
              </Text>
              <DataTable columns={res.columns.map((c) => c.name)} rows={res.rows} height={360} />
            </>
          )}
          {res.issues.filter((i) => i.level !== "error").length > 0 && <Issues issues={res.issues.filter((i) => i.level !== "error")} />}
        </>
      )}
    </Stack>
  );
}

export function SlidePreview({ text, number, period }: { text: string; number: number; period: string }) {
  const key = JSON.stringify({ text, number, period });
  const job = usePreviewJob<Schemas["SlidePreviewOut"]>(
    key,
    () => unwrap(api.POST("/api/preview/slide", { body: { text, slide: number, period: period || null, image: true } })),
    1200,
  );
  const res = job.result && job.resultKey && (JSON.parse(job.resultKey) as { number: number }).number === number ? job.result : null;
  const issues: Issue[] = res?.result.issues ?? [];
  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={5}>Слайд {number}: пробная сборка</Title>
        <Group gap="xs">
          {job.loading && <Loader size="xs" />}
          {res && <Badge variant="light">{periodLabel(res.result.period)}</Badge>}
        </Group>
      </Group>
      {job.loading && !res && (
        <Center h={200}>
          <Text size="sm" c="dimmed">
            Собираю слайд…
          </Text>
        </Center>
      )}
      {job.error != null && !(job.error instanceof ApiError && job.error.code === "cancelled") && <ErrorAlert error={job.error} />}
      {res && (
        <>
          {res.files.png ? (
            <Image src={withToken(res.files.png)} alt={`Слайд ${number}`} radius="sm" style={{ border: "1px solid var(--mantine-color-default-border)", opacity: job.loading ? 0.6 : 1 }} />
          ) : (
            <Alert color="gray" variant="light">
              {res.result.image_note ?? "Картинки слайда нет: на этом компьютере нет PowerPoint или LibreOffice."}
            </Alert>
          )}
          <Group gap="md">
            <Anchor size="sm" href={withToken(res.files.pptx)} download={`слайд_${number}.pptx`}>
              Скачать слайд .pptx
            </Anchor>
            <Text size="xs" c="dimmed">
              {res.result.seconds.toFixed(1)} с
            </Text>
          </Group>
          <Issues issues={issues} empty="Замечаний нет." />
        </>
      )}
    </Stack>
  );
}
