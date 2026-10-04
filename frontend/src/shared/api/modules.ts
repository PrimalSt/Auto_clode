// Модули и плагины: манифест с параметрами шагов, окон и блоков (для форм конструктора).
import { useQuery } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "./client";

export type PluginInfo = Schemas["PluginInfo"];

export const useModules = () =>
  useQuery({ queryKey: ["modules"], queryFn: () => unwrap(api.GET("/api/modules")), staleTime: 5 * 60_000 });

/** Плагины одного вида (step, window, aggregation, block, reader), которые работают. */
export function usePlugins(kind: PluginInfo["kind"]): PluginInfo[] {
  const modules = useModules();
  return (modules.data?.plugins?.plugins ?? []).filter((p) => p.kind === kind && p.status === "ok");
}
