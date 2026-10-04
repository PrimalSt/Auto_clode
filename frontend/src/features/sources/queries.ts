import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../../shared/api/client";

export const useSources = () => useQuery({ queryKey: ["sources"], queryFn: () => unwrap(api.GET("/api/sources")) });

export const useSource = (id: string) =>
  useQuery({ queryKey: ["sources", id], queryFn: () => unwrap(api.GET("/api/sources/{source_id}", { params: { path: { source_id: id } } })) });

export const useHistory = (id: string) =>
  useQuery({
    queryKey: ["sources", id, "history"],
    queryFn: () => unwrap(api.GET("/api/sources/{source_id}/history", { params: { path: { source_id: id } } })),
  });

export const useSourceVersions = (id: string) =>
  useQuery({
    queryKey: ["sources", id, "versions"],
    queryFn: () => unwrap(api.GET("/api/sources/{source_id}/versions", { params: { path: { source_id: id } } })),
  });

export const useUsage = (id: string) =>
  useQuery({
    queryKey: ["sources", id, "usage"],
    queryFn: () => unwrap(api.GET("/api/sources/{source_id}/usage", { params: { path: { source_id: id } } })),
  });
