// Небольшой редактор кода для полей конструктора: Python, SQL.
import Editor from "@monaco-editor/react";
import { Input } from "@mantine/core";
import { EDITOR_OPTIONS } from "../monaco/setup";

interface Props {
  label?: string;
  description?: string;
  language: "python" | "sql" | "yaml";
  value: string;
  onChange: (value: string) => void;
  height?: number;
  required?: boolean;
}

export function CodeEditor({ label, description, language, value, onChange, height, required }: Props) {
  const lines = value.split("\n").length;
  return (
    <Input.Wrapper label={label} description={description} withAsterisk={required}>
      <div style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 4, overflow: "hidden", marginTop: 4 }}>
        <Editor
          height={height ?? Math.min(480, Math.max(120, lines * 19 + 20))}
          language={language}
          value={value}
          onChange={(v) => onChange(v ?? "")}
          options={{ ...EDITOR_OPTIONS, tabSize: 4, lineNumbers: "on" }}
        />
      </div>
    </Input.Wrapper>
  );
}
