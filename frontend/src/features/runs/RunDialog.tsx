// Собрать отчёт: период, папка для копии; ход — в задании, итог — запись запуска.
import { Button, Checkbox, Group, Stack } from "@mantine/core";
import { useState } from "react";
import { ApiError, api, unwrap, type RunRecord } from "../../shared/api/client";
import { runJob } from "../../shared/api/jobs";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { JobProgress } from "../../shared/components/JobProgress";
import { PathInput } from "../../shared/components/PathInput";
import { PeriodSelect } from "../../shared/components/PeriodSelect";
import { RunResultView } from "./RunResultView";

const OUTPUT_KEY = "agen-run-output";

function remembered(): string {
  try {
    return window.localStorage.getItem(OUTPUT_KEY) ?? "";
  } catch {
    return "";
  }
}

export function RunDialog({ scenarioId, mainSource, dirty }: { scenarioId: string; mainSource: string | null; dirty?: boolean }) {
  const [period, setPeriod] = useState("");
  const [output, setOutput] = useState(remembered);
  const [acceptCast, setAcceptCast] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [run, setRun] = useState<RunRecord | null>(null);
  const start = async () => {
    setBusy(true);
    setError(null);
    setRun(null);
    try {
      window.localStorage.setItem(OUTPUT_KEY, output);
    } catch {
      /* окно без хранилища: папку не запомним */
    }
    try {
      const res = await runJob<RunRecord>(
        () =>
          unwrap(
            api.POST("/api/scenarios/{scenario_id}/runs", {
              params: { path: { scenario_id: scenarioId } },
              body: { period: period || null, output: output || null, accept_cast_errors: acceptCast },
            }),
          ),
        (j) => setJobId(j.id),
      );
      setRun(res);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
      setJobId(null);
    }
  };
  const castReview = error instanceof ApiError && error.code === "cast_review";
  return (
    <Stack>
      {dirty && <ErrorAlert error={new ApiError("draft", "Отчёт соберётся по сохранённой версии сценария: несохранённые правки в него не попадут.")} />}
      <PeriodSelect sourceId={mainSource} value={period} onChange={setPeriod} description="Пусто — последний загруженный период основного входа" />
      <PathInput label="Копия отчёта в папку" folder value={output} onChange={setOutput} description="Пусто — отчёт останется только в папке данных (его можно скачать)" />
      {castReview && <Checkbox label="Собрать, несмотря на значения, которые не привелись к типу столбца" checked={acceptCast} onChange={(e) => setAcceptCast(e.currentTarget.checked)} />}
      <JobProgress jobId={jobId} />
      <ErrorAlert error={error} />
      {run && <RunResultView run={run} />}
      <Group justify="flex-end">
        <Button onClick={start} loading={busy}>
          {run ? "Собрать ещё раз" : "Собрать отчёт"}
        </Button>
      </Group>
    </Stack>
  );
}
