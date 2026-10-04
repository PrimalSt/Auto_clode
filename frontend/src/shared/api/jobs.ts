// Задания сервера: состояние из потока событий, ожидание итога, отмена.
import { create } from "zustand";
import { ApiError, api, unwrap, type JobInfo } from "./client";

interface JobsState {
  jobs: Record<string, JobInfo>;
  put: (job: JobInfo) => void;
}

/** Последнее известное состояние заданий (события `job` приходят без итога). */
export const useJobs = create<JobsState>()((set) => ({
  jobs: {},
  put: (job) => set((s) => ({ jobs: { ...s.jobs, [job.id]: { ...s.jobs[job.id], ...job } } })),
}));

export const isFinished = (job: Pick<JobInfo, "status">): boolean =>
  job.status === "done" || job.status === "failed" || job.status === "cancelled";

const POLL_MS = 2000;

/** Дождаться конца задания и вернуть его с итогом. Ход идёт событиями; если поток событий
 * прервался, состояние раз в две секунды запрашивается само. */
export function waitJob(id: string, signal?: AbortSignal): Promise<JobInfo> {
  return new Promise((resolve, reject) => {
    let done = false;
    const finish = async () => {
      if (done) return;
      done = true;
      cleanup();
      try {
        const job = await unwrap(api.GET("/api/jobs/{job_id}", { params: { path: { job_id: id } } }));
        useJobs.getState().put(job);
        resolve(job);
      } catch (e) {
        reject(e);
      }
    };
    const unsubscribe = useJobs.subscribe((s) => {
      const job = s.jobs[id];
      if (job && isFinished(job)) void finish();
    });
    const timer = window.setInterval(async () => {
      try {
        const job = await unwrap(api.GET("/api/jobs/{job_id}", { params: { path: { job_id: id } } }));
        useJobs.getState().put(job);
      } catch {
        /* сервер недоступен: подождём */
      }
    }, POLL_MS);
    const onAbort = () => {
      done = true;
      cleanup();
      reject(new ApiError("cancelled", "Ожидание отменено"));
    };
    signal?.addEventListener("abort", onAbort);
    function cleanup() {
      unsubscribe();
      window.clearInterval(timer);
      signal?.removeEventListener("abort", onAbort);
    }
    const known = useJobs.getState().jobs[id];
    if (known && isFinished(known)) void finish();
  });
}

/** Ошибка задания как ApiError (код, текст, подсказка, подробности — например, сверка при schema_review). */
export function jobError(job: JobInfo): ApiError {
  const e = job.error;
  if (job.status === "cancelled") return new ApiError("cancelled", e?.message ?? "Задание отменено");
  return new ApiError(e?.code ?? "internal", e?.message ?? "Задание не выполнено", e?.hint ?? null, (e?.details as Record<string, unknown>) ?? {});
}

/** Поставить задание и дождаться итога; ошибка задания — исключение ApiError. */
export async function runJob<T>(submit: () => Promise<JobInfo>, onStart?: (job: JobInfo) => void): Promise<T> {
  const started = await submit();
  useJobs.getState().put(started);
  onStart?.(started);
  const job = await waitJob(started.id);
  if (job.status !== "done") throw jobError(job);
  return job.result as T;
}

export async function cancelJob(id: string): Promise<void> {
  const job = await unwrap(api.POST("/api/jobs/{job_id}/cancel", { params: { path: { job_id: id } } }));
  useJobs.getState().put(job);
}
