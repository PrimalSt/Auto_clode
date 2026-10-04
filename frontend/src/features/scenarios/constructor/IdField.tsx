import { TextInput } from "@mantine/core";
import { useEffect, useState } from "react";

const ID = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** id узла: меняется, когда поле теряет фокус (ссылки на узел переименовываются тогда же). */
export function IdField({ value, taken, onCommit, label = "id", description }: { value: string; taken: string[]; onCommit: (id: string) => void; label?: string; description?: string }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const error = text !== value && (!ID.test(text) ? "Латиница, цифры и «_», с буквы" : taken.includes(text) ? "Такой id уже есть" : null);
  return (
    <TextInput
      label={label}
      description={description}
      styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
      value={text}
      error={error || undefined}
      onChange={(e) => setText(e.currentTarget.value.trim())}
      onBlur={() => {
        if (text !== value && !error) onCommit(text);
        else setText(value);
      }}
      onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLInputElement).blur()}
    />
  );
}
