import { Alert, Badge, Button, Group, Select, Stack, Table, Text } from "@mantine/core";
import { useMemo, useState } from "react";
import type { MappingCandidate, ReconcileResult, SourceSpec } from "../../shared/api/client";
import { DTYPES } from "../../shared/labels";

export interface MappingDecision {
  /** Название в файле → id столбца источника. */
  mapping: Record<string, string>;
  /** id столбцов, которые оставить пустыми в этой загрузке. */
  declined: string[];
}

const EMPTY = "__empty__";

interface Row {
  file: string;
  id: string;
  required: boolean;
  candidates: MappingCandidate[];
  proposed: string | null;
  dependents: string[];
}

function rows(files: Record<string, ReconcileResult>): Row[] {
  const out: Row[] = [];
  for (const [file, r] of Object.entries(files)) {
    const ids = [...new Set([...(r.review ?? []), ...(r.missing_required ?? [])])];
    for (const id of ids) {
      const proposed = Object.entries(r.proposed ?? {}).find(([, c]) => c === id)?.[0] ?? null;
      out.push({
        file,
        id,
        required: (r.missing_required ?? []).includes(id),
        candidates: r.candidates?.[id] ?? [],
        proposed,
        dependents: r.dependents?.[id] ?? [],
      });
    }
  }
  return out;
}

function scoreColor(s: number): string {
  return s >= 0.8 ? "teal" : s >= 0.6 ? "yellow" : "gray";
}

/** Экран сопоставления (F-602…F-606): «ожидалось» ↔ «в файле», подсказки с уверенностью,
 * «Принять все». Подтверждённые названия сервер запомнит в источнике. */
export function MappingReview({
  source,
  files,
  onSubmit,
  onCancel,
}: {
  source: SourceSpec;
  files: Record<string, ReconcileResult>;
  onSubmit: (d: MappingDecision) => void;
  onCancel: () => void;
}) {
  const list = useMemo(() => rows(files), [files]);
  const [choice, setChoice] = useState<Record<string, string | null>>(() =>
    Object.fromEntries(list.map((r) => [`${r.file}|${r.id}`, r.proposed])),
  );
  const names = new Map(source.columns.map((c) => [c.id, c]));
  const undecided = list.filter((r) => !choice[`${r.file}|${r.id}`]);
  const blockedRequired = list.filter((r) => r.required && choice[`${r.file}|${r.id}`] === EMPTY);
  const warnings = Object.values(files).flatMap((r) => r.warnings ?? []);

  const submit = () => {
    const mapping: Record<string, string> = {};
    const declined: string[] = [];
    for (const r of list) {
      const v = choice[`${r.file}|${r.id}`];
      if (v === EMPTY) declined.push(r.id);
      else if (v) mapping[v] = r.id;
    }
    onSubmit({ mapping, declined: [...new Set(declined)] });
  };

  return (
    <Stack>
      <Text size="sm">
        В файле нет столбцов, которые ждёт источник «{source.name}». Выберите, какой столбец файла им соответствует.
        Подтверждённые названия запомнятся, и в следующий раз сопоставятся сами.
      </Text>
      <Table withTableBorder verticalSpacing="sm">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Ожидалось</Table.Th>
            <Table.Th>В файле</Table.Th>
            {Object.keys(files).length > 1 && <Table.Th>Файл</Table.Th>}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {list.map((r) => {
            const key = `${r.file}|${r.id}`;
            const col = names.get(r.id);
            const data = [
              ...r.candidates.map((c) => ({
                value: c.file_name,
                label: `${c.file_name} — ${Math.round(c.score * 100)}%${c.dtype ? `, ${DTYPES[c.dtype] ?? c.dtype}` : ""}`,
              })),
              { value: EMPTY, label: r.required ? "оставить пустым (отчёт без него не соберётся)" : "оставить пустым в этой загрузке" },
            ];
            const best = r.candidates[0];
            return (
              <Table.Tr key={key}>
                <Table.Td>
                  <Text fw={500}>{col?.name ?? r.id}</Text>
                  <Group gap={6}>
                    <Text size="xs" c="dimmed">
                      {r.id}, {DTYPES[col?.dtype ?? "string"]}
                    </Text>
                    {r.required && (
                      <Badge size="xs" color="red" variant="light">
                        нужен сценариям
                      </Badge>
                    )}
                  </Group>
                  {r.dependents.map((d, i) => (
                    <Text key={i} size="xs" c="dimmed">
                      {d}
                    </Text>
                  ))}
                </Table.Td>
                <Table.Td>
                  <Select
                    data={data}
                    value={choice[key] ?? null}
                    placeholder={r.candidates.length ? "Выберите столбец" : "Похожих столбцов нет"}
                    onChange={(v) => setChoice((s) => ({ ...s, [key]: v }))}
                  />
                  {best && !r.proposed && r.candidates.length > 1 && (
                    <Text size="xs" c="dimmed" mt={2}>
                      Несколько похожих столбцов: выберите сами.
                    </Text>
                  )}
                  {best && (
                    <Badge mt={4} size="xs" variant="light" color={scoreColor(best.score)}>
                      лучшее сходство {Math.round(best.score * 100)}%
                    </Badge>
                  )}
                </Table.Td>
                {Object.keys(files).length > 1 && <Table.Td>{r.file}</Table.Td>}
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
      {warnings.length > 0 && (
        <Alert color="yellow" title="Стоит проверить">
          {warnings.map((w, i) => (
            <Text key={i} size="sm">
              {w}
            </Text>
          ))}
        </Alert>
      )}
      {blockedRequired.length > 0 && (
        <Text size="sm" c="red">
          Без столбцов {blockedRequired.map((r) => names.get(r.id)?.name ?? r.id).join(", ")} загрузка не пройдёт: они
          нужны сценариям.
        </Text>
      )}
      <Group justify="space-between">
        <Button variant="default" onClick={onCancel}>
          Отменить загрузку
        </Button>
        <Group gap="xs">
          <Button
            variant="light"
            disabled={!list.some((r) => r.proposed)}
            onClick={() => setChoice((s) => ({ ...s, ...Object.fromEntries(list.filter((r) => r.proposed).map((r) => [`${r.file}|${r.id}`, r.proposed])) }))}
          >
            Принять все предложенные
          </Button>
          <Button onClick={submit} disabled={undecided.length > 0 || blockedRequired.length > 0}>
            Загрузить
          </Button>
        </Group>
      </Group>
    </Stack>
  );
}
