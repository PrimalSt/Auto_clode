// Поток событий сервера (SSE): ход заданий и «данные изменились» — перечитать раздел.
import type { QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { create } from "zustand";
import { connection, type JobInfo } from "./client";
import { useJobs } from "./jobs";

interface ConnectionState {
  online: boolean;
  set: (online: boolean) => void;
}

export const useConnection = create<ConnectionState>()((set) => ({ online: false, set: (online) => set({ online }) }));

/** Что перечитать, когда изменились данные раздела. Ключи запросов начинаются с имени раздела. */
export function invalidate(queryClient: QueryClient, what: string, id: string | null): void {
  if (what === "all") {
    void queryClient.invalidateQueries();
    return;
  }
  void queryClient.invalidateQueries({ queryKey: [what] });
  if (what === "uploads" && id) void queryClient.invalidateQueries({ queryKey: ["sources", id] });
  if (what === "themes" || what === "runs") void queryClient.invalidateQueries({ queryKey: ["scenarios"] });
}

/** Подписка на поток событий на всё время жизни окна; браузер переподключается сам и
 * передаёт номер последнего события (Last-Event-ID), так что пропущенное приходит. */
export function useServerEvents(queryClient: QueryClient): void {
  useEffect(() => {
    const token = connection.token;
    const url = `${connection.baseUrl}/api/events${token ? `?token=${encodeURIComponent(token)}` : ""}`;
    const source = new EventSource(url);
    const setOnline = useConnection.getState().set;
    source.onopen = () => setOnline(true);
    source.onerror = () => setOnline(false);
    source.addEventListener("job", (e) => {
      useJobs.getState().put(JSON.parse((e as MessageEvent).data) as JobInfo);
    });
    source.addEventListener("changed", (e) => {
      const data = JSON.parse((e as MessageEvent).data) as { what: string; id: string | null };
      invalidate(queryClient, data.what, data.id);
    });
    return () => source.close();
  }, [queryClient]);
}
