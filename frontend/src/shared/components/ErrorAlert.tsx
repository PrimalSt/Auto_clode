import { Alert, Code, Collapse, Text, UnstyledButton } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { useState } from "react";
import { ApiError } from "../api/client";

/** Ошибка так, как её показать: текст, подсказка, подробности по клику. */
export function ErrorAlert({ error, title }: { error: unknown; title?: string }) {
  const [open, setOpen] = useState(false);
  if (!error) return null;
  const e = error instanceof ApiError ? error : new ApiError("internal", error instanceof Error ? error.message : String(error));
  const trace = typeof e.details.traceback === "string" ? e.details.traceback : null;
  return (
    <Alert color="red" icon={<IconAlertTriangle size={18} />} title={title}>
      <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
        {e.message}
      </Text>
      {e.hint && (
        <Text size="sm" c="dimmed" mt={4}>
          {e.hint}
        </Text>
      )}
      {trace && (
        <>
          <UnstyledButton mt={6} onClick={() => setOpen((o) => !o)}>
            <Text size="xs" c="dimmed" td="underline">
              {open ? "Скрыть подробности" : "Подробности"}
            </Text>
          </UnstyledButton>
          <Collapse expanded={open}>
            <Code block mt={6} style={{ maxHeight: 240, overflow: "auto" }}>
              {trace}
            </Code>
          </Collapse>
        </>
      )}
    </Alert>
  );
}
