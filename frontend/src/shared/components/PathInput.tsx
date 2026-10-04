import { ActionIcon, Group, TextInput, Tooltip } from "@mantine/core";
import { IconFolderOpen } from "@tabler/icons-react";
import { useFileDrop, withExtensions } from "../shell";

interface Props {
  label: string;
  value: string;
  onChange: (value: string) => void;
  description?: string;
  placeholder?: string;
  folder?: boolean;
  extensions?: string[];
  required?: boolean;
  error?: string | null;
}

/** Путь к файлу или папке на этом компьютере. В окне оболочки — кнопка с системным диалогом,
 * а файл можно перетащить в окно; в браузере путь вводится текстом (браузер не отдаёт пути
 * выбранных файлов). */
export function PathInput({ label, value, onChange, description, placeholder, folder, extensions, required, error }: Props) {
  const bridge = window.__AGEN__;
  const canPick = folder ? !!bridge?.pickFolder : !!bridge?.pickFiles;
  const drop = useFileDrop((paths) => {
    const ok = withExtensions(paths, extensions);
    if (ok.length) onChange(ok[0]);
  }, !folder);
  const pick = async () => {
    if (folder) {
      const p = await bridge?.pickFolder?.({ title: label });
      if (p) onChange(p);
    } else {
      const p = await bridge?.pickFiles?.({ title: label, extensions, multiple: false });
      if (p && p.length) onChange(p[0]);
    }
  };
  return (
    <Group align="flex-end" gap="xs" wrap="nowrap">
      <TextInput
        style={{ flex: 1 }}
        label={label}
        description={description}
        placeholder={placeholder ?? (folder ? "C:\\Отчёты" : "C:\\Выгрузки\\файл.xlsx")}
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        required={required}
        error={error}
        styles={drop.over ? { input: { borderColor: "var(--mantine-color-blue-6)", background: "var(--mantine-color-blue-light)" } } : undefined}
      />
      {canPick && (
        <Tooltip label="Выбрать">
          <ActionIcon size="lg" variant="default" onClick={pick} aria-label="Выбрать">
            <IconFolderOpen size={18} />
          </ActionIcon>
        </Tooltip>
      )}
    </Group>
  );
}

/** Несколько путей: по одному в строке (части одной выгрузки или несколько выгрузок). */
export function splitPaths(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((s) => s.trim().replace(/^"(.*)"$/, "$1"))
    .filter(Boolean);
}
