import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../../shared/api/client";

export const useRuns = (scenario?: string, limit = 200) =>
  useQuery({
    queryKey: ["runs", scenario ?? "", limit],
    queryFn: () => unwrap(api.GET("/api/runs", { params: { query: { ...(scenario ? { scenario } : {}), limit } } })),
  });

export const outputUrl = (runId: string) => `/api/runs/${encodeURIComponent(runId)}/output`;
