import { Badge, Group, List, Text } from "@mantine/core";
import type { Issue } from "../api/client";

const COLORS: Record<string, string> = { error: "red", warning: "yellow", info: "blue" };
const LABELS: Record<string, string> = { error: "ошибка", warning: "предупреждение", info: "заметка" };

/** Замечания проверки и сборки: уровень, где, что. */
export function Issues({ issues, empty }: { issues: Issue[]; empty?: string }) {
  if (!issues.length) return empty ? <Text size="sm" c="dimmed">{empty}</Text> : null;
  return (
    <List spacing={6} listStyleType="none">
      {issues.map((i, n) => (
        <List.Item key={n}>
          <Group gap={8} wrap="nowrap" align="flex-start">
            <Badge color={COLORS[i.level] ?? "gray"} variant="light" size="sm" style={{ flexShrink: 0 }}>
              {LABELS[i.level] ?? i.level}
            </Badge>
            <Text size="sm">
              {i.node && (
                <Text span c="dimmed" size="sm">
                  {i.node}:{" "}
                </Text>
              )}
              {i.message}
            </Text>
          </Group>
        </List.Item>
      ))}
    </List>
  );
}
