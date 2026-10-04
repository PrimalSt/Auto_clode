import { Alert, Button, Group, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { api, unwrap, type Schemas } from "../../shared/api/client";
import { runJob } from "../../shared/api/jobs";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { JobProgress } from "../../shared/components/JobProgress";
import { PathInput } from "../../shared/components/PathInput";

type Out = Schemas["ThemeImportOut"];

/** Итог импорта: новая версия, тот же файл или сценарии, оставшиеся на прежней версии. */
export function ImportSummary({ out }: { out: Out }) {
  const lost = Object.entries(out.lost);
  return (
    <Stack gap="xs">
      {out.skipped ? (
        <Text size="sm">
          {out.matched ? `Этот файл уже загружен (версия ${out.matched}) — новой версии нет.` : "Роли макетов не изменились — новой версии нет."}
        </Text>
      ) : (
        <Text size="sm">
          Шаблон «{out.record.name}», версия {out.record.version}.
          {out.scenarios.length > 0 && ` Перешли на неё сценарии: ${out.scenarios.join(", ")}.`}
        </Text>
      )}
      {lost.map(([sid, problems]) => (
        <Alert key={sid} color="yellow" title={`Сценарий «${sid}» остался на прежней версии шаблона`}>
          {problems.map((p, i) => (
            <Text key={i} size="sm">
              {p}
            </Text>
          ))}
        </Alert>
      ))}
    </Stack>
  );
}

/** Загрузка шаблона .pptx/.potx: новый шаблон или новая версия существующего (``themeId``). */
export function ThemeImport({ themeId, onDone }: { themeId?: string; onDone?: (out: Out) => void }) {
  const [path, setPath] = useState("");
  const [id, setId] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [out, setOut] = useState<Out | null>(null);
  const start = async () => {
    setBusy(true);
    setError(null);
    setOut(null);
    try {
      const res = await runJob<Out>(
        () =>
          themeId
            ? unwrap(api.POST("/api/themes/{theme_id}/reimport", { params: { path: { theme_id: themeId } }, body: { path } }))
            : unwrap(api.POST("/api/themes", { body: { path, id: id || null } })),
        (j) => setJobId(j.id),
      );
      setOut(res);
      onDone?.(res);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
      setJobId(null);
    }
  };
  return (
    <Stack>
      <PathInput label="Файл шаблона" value={path} onChange={setPath} extensions={["pptx", "potx"]} required />
      {!themeId && (
        <TextInput
          label="id шаблона"
          description="Пусто — по имени файла. id существующего шаблона — его новая версия."
          value={id}
          onChange={(e) => setId(e.currentTarget.value.trim())}
        />
      )}
      <JobProgress jobId={jobId} />
      <ErrorAlert error={error} />
      {out && <ImportSummary out={out} />}
      <Group justify="flex-end">
        <Button onClick={start} loading={busy} disabled={!path}>
          Загрузить
        </Button>
      </Group>
    </Stack>
  );
}
