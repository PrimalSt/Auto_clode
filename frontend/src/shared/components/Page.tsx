import { Group, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";

/** Заголовок раздела с действиями справа. */
export function Page({ title, subtitle, actions, children }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>{title}</Title>
          {subtitle && (
            <Text c="dimmed" size="sm">
              {subtitle}
            </Text>
          )}
        </div>
        {actions && <Group gap="xs">{actions}</Group>}
      </Group>
      {children}
    </Stack>
  );
}
