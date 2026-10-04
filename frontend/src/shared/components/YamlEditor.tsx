// Редактор YAML сценария: Monaco с проверкой и дополнением по JSON Schema сценария с сервера.
import Editor from "@monaco-editor/react";
import { useEffect } from "react";
import { useScenarioSchema } from "../api/schemas";
import { EDITOR_OPTIONS, SCENARIO_MODEL_PREFIX, setScenarioSchema } from "../monaco/setup";

interface Props {
  /** Имя модели: у каждого сценария и версии своя история правок. */
  name: string;
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  height?: number | string;
}

export function YamlEditor({ name, value, onChange, readOnly, height = "65vh" }: Props) {
  const schema = useScenarioSchema();
  useEffect(() => {
    if (schema.data) setScenarioSchema(schema.data);
  }, [schema.data]);
  return (
    <Editor
      height={height}
      language="yaml"
      path={`${SCENARIO_MODEL_PREFIX}${name}.yaml`}
      value={value}
      onChange={(v) => onChange?.(v ?? "")}
      options={{
        ...EDITOR_OPTIONS,
        readOnly,
        tabSize: 2,
        wordWrap: "on",
        quickSuggestions: { other: true, comments: false, strings: true },
      }}
    />
  );
}
