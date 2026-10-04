import { Box, Group, Text, Tooltip } from "@mantine/core";
import type { Schemas } from "../../shared/api/client";
import { periodLabel } from "../../shared/format";

const COLORS: Record<string, string> = {
  covered: "var(--mantine-color-teal-5)",
  overlap: "var(--mantine-color-orange-5)",
  gap: "var(--mantine-color-gray-3)",
};
const STATES: Record<string, string> = { covered: "загружено", overlap: "наложение загрузок", gap: "пропуск" };

/** Шкала покрытия истории: какие периоды загружены, где пропуски и наложения. */
export function Coverage({ report }: { report: Schemas["CoverageReport"] }) {
  if (!report.cells.length) return null;
  return (
    <Box>
      <Group gap={2} wrap="nowrap" style={{ overflowX: "auto" }}>
        {report.cells.map((c, i) => (
          <Tooltip key={i} label={`${periodLabel(c.period)}: ${STATES[c.state]}${c.uploads.length > 1 ? ` (${c.uploads.length})` : ""}`}>
            <Box style={{ flex: "1 0 14px", minWidth: 14, height: 22, borderRadius: 3, background: COLORS[c.state] }} />
          </Tooltip>
        ))}
      </Group>
      <Group justify="space-between" mt={4}>
        <Text size="xs" c="dimmed">
          {periodLabel(report.cells[0].period)}
        </Text>
        <Text size="xs" c="dimmed">
          {report.gaps.length ? `пропусков: ${report.gaps.length}` : "без пропусков"}
          {report.overlaps.length ? `, наложений: ${report.overlaps.length}` : ""}
        </Text>
        <Text size="xs" c="dimmed">
          {periodLabel(report.cells[report.cells.length - 1].period)}
        </Text>
      </Group>
    </Box>
  );
}
