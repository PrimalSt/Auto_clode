import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../../shared/api/client";

export const useThemes = () => useQuery({ queryKey: ["themes"], queryFn: () => unwrap(api.GET("/api/themes")) });

export const useTheme = (id: string) =>
  useQuery({
    queryKey: ["themes", id],
    queryFn: () => unwrap(api.GET("/api/themes/{theme_id}", { params: { path: { theme_id: id } } })),
    enabled: !!id,
  });

export const useThemeVersions = (id: string) =>
  useQuery({
    queryKey: ["themes", id, "versions"],
    queryFn: () => unwrap(api.GET("/api/themes/{theme_id}/versions", { params: { path: { theme_id: id } } })),
  });
