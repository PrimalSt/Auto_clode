import { Button, Group, Progress, Stack, Text } from "@mantine/core";
import { cancelJob, isFinished, useJobs } from "../api/jobs";
import { bytes, count } from "../format";

/** Ход задания: этап, сколько сделано, кнопка «Отменить». */
export function JobProgress({ jobId, cancellable = true }: { jobId: string | null; cancellable?: boolean }) {
  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  if (!jobId || !job || isFinished(job)) return null;
  const p = job.progress;
  const share = p && p.total ? Math.min(100, (100 * p.done) / p.total) : null;
  const amount = p ? (p.unit === "bytes" ? `${bytes(p.done)}${p.total ? ` из ${bytes(p.total)}` : ""}` : `${count(p.done)}${p.total ? ` из ${count(p.total)}` : ""}`) : "";
  return (
    <Stack gap={4}>
      <Group justify="space-between">
        <Text size="sm">{job.status === "queued" ? "Ждёт очереди…" : p ? `${p.stage}${amount ? `: ${amount}` : ""}` : job.title || "Выполняется…"}</Text>
        {cancellable && (
          <Button size="compact-xs" variant="subtle" color="red" onClick={() => void cancelJob(jobId)}>
            Отменить
          </Button>
        )}
      </Group>
      <Progress value={share ?? 100} animated={share == null} striped={share == null} />
    </Stack>
  );
}
