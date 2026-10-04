import { Badge, Group, MultiSelect, Select, Stack, Table, TagsInput, Text, TextInput } from "@mantine/core";
import type { ColumnSpec, Schemas, SourceSpec } from "../../shared/api/client";
import { DTYPES, OVERLAP, PERIOD_UNITS, options } from "../../shared/labels";

type Snapshot = Schemas["SchemaSnapshot"];

interface Props {
  spec: SourceSpec;
  onChange: (spec: SourceSpec) => void;
  snapshot?: Snapshot | null;
  /** id столбцов, которые есть в загрузках: их id не меняется. */
  lockedIds?: boolean;
}

/** Настройки источника: столбцы (название, id, тип, другие названия), период, ключи, правило пересечения. */
export function ColumnsEditor({ spec, onChange, snapshot, lockedIds }: Props) {
  const samples = new Map((snapshot?.columns ?? []).map((c) => [c.source_name, c]));
  const setColumn = (i: number, patch: Partial<ColumnSpec>) =>
    onChange({ ...spec, columns: spec.columns.map((c, n) => (n === i ? { ...c, ...patch } : c)) });
  const dateCols = spec.columns.filter((c) => c.dtype === "date" || c.dtype === "datetime");
  const fromUpload = spec.period_from === "upload";
  return (
    <Stack gap="md">
      <Group grow align="flex-start">
        <Select
          label="Период строк"
          data={[
            { value: "column", label: "по датам столбца" },
            { value: "upload", label: "задаётся при загрузке (срез)" },
          ]}
          value={spec.period_from ?? "column"}
          onChange={(v) => onChange({ ...spec, period_from: (v ?? "column") as SourceSpec["period_from"] })}
          allowDeselect={false}
        />
        <Select
          label={fromUpload ? "Столбец периода (заполняется началом периода)" : "Столбец с датами"}
          data={(fromUpload ? spec.columns : dateCols).map((c) => ({ value: c.id, label: `${c.name} (${c.id})` }))}
          value={spec.period_column}
          onChange={(v) => v && onChange({ ...spec, period_column: v })}
          allowDeselect={false}
          searchable
        />
        <Select
          label="Единица периода"
          data={options(PERIOD_UNITS)}
          value={spec.period_type ?? "month"}
          onChange={(v) => v && onChange({ ...spec, period_type: v as SourceSpec["period_type"] })}
          allowDeselect={false}
        />
      </Group>
      <Group grow align="flex-start">
        <Select
          label="Если период уже загружен"
          data={options(OVERLAP)}
          value={spec.overlap_policy ?? "replace_period"}
          description={OVERLAP[spec.overlap_policy ?? "replace_period"]?.hint}
          onChange={(v) => v && onChange({ ...spec, overlap_policy: v as SourceSpec["overlap_policy"] })}
          allowDeselect={false}
        />
        <MultiSelect
          label="Ключи строки"
          description="Для объединения по ключам и поиска дубликатов"
          data={spec.columns.map((c) => ({ value: c.id, label: c.name }))}
          value={spec.keys ?? []}
          onChange={(keys) => onChange({ ...spec, keys })}
          searchable
          clearable
        />
      </Group>
      <Table striped withTableBorder verticalSpacing={4}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Название в файле</Table.Th>
            <Table.Th>id</Table.Th>
            <Table.Th w={150}>Тип</Table.Th>
            <Table.Th>Другие названия</Table.Th>
            {snapshot && <Table.Th>Примеры</Table.Th>}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {spec.columns.map((c, i) => {
            const s = samples.get(c.name);
            return (
              <Table.Tr key={i}>
                <Table.Td>
                  <TextInput size="xs" value={c.name} onChange={(e) => setColumn(i, { name: e.currentTarget.value })} />
                </Table.Td>
                <Table.Td>
                  <TextInput
                    size="xs"
                    value={c.id}
                    disabled={lockedIds}
                    onChange={(e) => setColumn(i, { id: e.currentTarget.value })}
                    error={/^[a-z_][a-z0-9_]*$/.test(c.id) ? undefined : true}
                  />
                </Table.Td>
                <Table.Td>
                  <Select
                    size="xs"
                    data={options(DTYPES)}
                    value={c.dtype ?? "string"}
                    onChange={(v) => v && setColumn(i, { dtype: v as ColumnSpec["dtype"] })}
                    allowDeselect={false}
                  />
                </Table.Td>
                <Table.Td>
                  <TagsInput size="xs" value={c.aliases ?? []} onChange={(aliases) => setColumn(i, { aliases })} />
                </Table.Td>
                {snapshot && (
                  <Table.Td>
                    <Text size="xs" c="dimmed" lineClamp={1}>
                      {s?.sample.slice(0, 3).join(" · ")}
                    </Text>
                    {s?.parsed_share != null && s.parsed_share < 1 && (
                      <Badge size="xs" color="yellow" variant="light">
                        распознано {Math.round(s.parsed_share * 100)}%
                      </Badge>
                    )}
                  </Table.Td>
                )}
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}
