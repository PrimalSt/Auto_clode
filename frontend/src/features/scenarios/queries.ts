import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../../shared/api/client";

export const useScenarios = () => useQuery({ queryKey: ["scenarios"], queryFn: () => unwrap(api.GET("/api/scenarios")) });

export const useScenario = (id: string) =>
  useQuery({
    queryKey: ["scenarios", id],
    queryFn: () => unwrap(api.GET("/api/scenarios/{scenario_id}", { params: { path: { scenario_id: id } } })),
  });

export const useScenarioVersions = (id: string) =>
  useQuery({
    queryKey: ["scenarios", id, "versions"],
    queryFn: () => unwrap(api.GET("/api/scenarios/{scenario_id}/versions", { params: { path: { scenario_id: id } } })),
  });

/** Текст YAML версии (по умолчанию — текущей), как его сохранили. */
export function scenarioText(id: string, version?: number): Promise<string> {
  return unwrap(
    api.GET("/api/scenarios/{scenario_id}/yaml", {
      params: { path: { scenario_id: id }, query: version ? { version } : {} },
      parseAs: "text",
    }),
  ) as Promise<string>;
}

export const useScenarioText = (id: string, version?: number) =>
  useQuery({ queryKey: ["scenarios", id, "yaml", version ?? "current"], queryFn: () => scenarioText(id, version) });
