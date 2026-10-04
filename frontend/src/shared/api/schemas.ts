import { useQuery } from "@tanstack/react-query";
import type { JsonSchema } from "../../features/scenarios/constructor/SchemaForm";
import { api, unwrap } from "./client";

/** Схема сценария для редактора и конструктора (с параметрами модулей; перечитывается при
 * перезапуске модулей). */
export const useScenarioSchema = () =>
  useQuery({
    queryKey: ["modules", "schema", "scenario"],
    queryFn: async () => (await unwrap(api.GET("/api/schemas/{name}", { params: { path: { name: "scenario" } } }))) as JsonSchema,
    staleTime: 5 * 60_000,
  });
