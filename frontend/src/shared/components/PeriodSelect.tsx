import { Autocomplete } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../api/client";
import { periodKey, periodLabel } from "../format";

interface Props {
  /** Источник основного входа: из его истории берутся загруженные периоды. */
  sourceId: string | null | undefined;
  value: string;
  onChange: (value: string) => void;
  label?: string;
  description?: string;
  size?: string;
  w?: number | string;
}

/** Отчётный период: загруженные периоды основного источника или своя запись (2026-03,
 * 2026-Q1, 2026-03-01..2026-03-15). Пусто — последний загруженный. */
export function PeriodSelect({ sourceId, value, onChange, label = "Отчётный период", description, size, w }: Props) {
  const history = useQuery({
    queryKey: ["sources", sourceId ?? "", "history"],
    queryFn: () => unwrap(api.GET("/api/sources/{source_id}/history", { params: { path: { source_id: sourceId ?? "" } } })),
    enabled: !!sourceId,
  });
  const cells = (history.data?.coverage?.cells ?? []).filter((c) => c.state !== "gap");
  const data = [...cells].reverse().map((c) => ({ value: periodKey(c.period), label: `${periodLabel(c.period)} (${periodKey(c.period)})` }));
  return (
    <Autocomplete
      label={label}
      description={description}
      size={size}
      w={w}
      placeholder="последний загруженный"
      data={data}
      value={value}
      onChange={(v) => onChange(v.replace(/^.*\((.+)\)$/, "$1"))}
      clearable
    />
  );
}
