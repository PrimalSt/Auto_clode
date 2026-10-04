import { ActionIcon, Group, TextInput, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconFolderOpen } from "@tabler/icons-react";
import { fileName } from "../format";
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
    const message = dropMessage(paths, ok, extensions);
    if (message) notifications.show({ color: "yellow", title: label, message });
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

/** Что сказать, если из перетащенных файлов поле взяло не все: не те расширения пропущены, а из
 * нескольких подходящих взят первый (поле — для одного файла). */
export function dropMessage(paths: string[], ok: string[], extensions?: string[]): string | null {
  const parts: string[] = [];
  const skipped = paths.filter((p) => !ok.includes(p));
  if (skipped.length) {
    const want = extensions?.length ? ` (нужен файл ${extensions.map((e) => "." + e.replace(/^\./, "")).join(", ")})` : "";
    parts.push(`Пропущены${want}: ${skipped.map(fileName).join(", ")}`);
  }
  if (ok.length > 1) parts.push(`Поле принимает один файл: взят ${fileName(ok[0])}`);
  return parts.length ? parts.join(". ") : null;
}

/** Несколько путей: по одному в строке (части одной выгрузки или несколько выгрузок). */
export function splitPaths(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((s) => s.trim().replace(/^"(.*)"$/, "$1"))
    .filter(Boolean);
}
