import { Button, Checkbox, Group, Modal, Radio, Stack, Text, TextInput, Textarea } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { ApiError, api, unwrap, type Issue, type ReconcileResult, type Schemas, type SourceSpec } from "../../shared/api/client";
import { runJob } from "../../shared/api/jobs";
import { ErrorAlert } from "../../shared/components/ErrorAlert";
import { Issues } from "../../shared/components/Issues";
import { JobProgress } from "../../shared/components/JobProgress";
import { splitPaths } from "../../shared/components/PathInput";
import { fileName } from "../../shared/format";
import { OVERLAP } from "../../shared/labels";
import { MappingReview, type MappingDecision } from "./MappingReview";

type UploadIn = Schemas["UploadIn"];
type UploadOut = Schemas["UploadOut"];

/** Что спросить у пользователя, прежде чем повторить загрузку. */
type Question =
  | { kind: "mapping"; files: Record<string, ReconcileResult> }
  | { kind: "overlap"; message: string }
  | { kind: "again"; message: string };

interface Pending {
  body: UploadIn;
  question: Question;
  resolve: (body: UploadIn | null) => void;
}

/** Загрузка выгрузок: прогресс и отмена, а если нужен выбор (сопоставление, пересечение периодов,
 * повторная загрузка того же файла) — спросить и отправить загрузку снова. */
export function UploadPanel({ source, onDone }: { source: SourceSpec; onDone?: () => void }) {
  const [text, setText] = useState("");
  const [parts, setParts] = useState(false);
  const [period, setPeriod] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [pending, setPending] = useState<Pending | null>(null);
  const [policy, setPolicy] = useState<string>("replace_period");

  const ask = (body: UploadIn, question: Question) =>
    new Promise<UploadIn | null>((resolve) => setPending({ body, question, resolve }));
  const answer = (body: UploadIn | null) => {
    pending?.resolve(body);
    setPending(null);
  };

  const uploadOne = async (first: UploadIn): Promise<UploadOut | null> => {
    let body = first;
    for (;;) {
      try {
        return await runJob<UploadOut>(
          () => unwrap(api.POST("/api/sources/{source_id}/uploads", { params: { path: { source_id: source.id } }, body })),
          (j) => setJobId(j.id),
        );
      } catch (e) {
        if (!(e instanceof ApiError)) throw e;
        let next: UploadIn | null = null;
        const files = e.details.files as Record<string, ReconcileResult> | undefined;
        if ((e.code === "schema_review" || e.code === "schema_blocked") && files && Object.keys(files).length) {
          next = await ask(body, { kind: "mapping", files });
        } else if (e.code === "overlap_choice") {
          next = await ask(body, { kind: "overlap", message: e.message });
        } else if (e.code === "already_exists") {
          next = await ask(body, { kind: "again", message: e.message });
        } else {
          throw e;
        }
        if (next === null) return null;
        body = next;
      } finally {
        setJobId(null);
      }
    }
  };

  const start = async () => {
    const paths = splitPaths(text);
    if (!paths.length) return;
    setBusy(true);
    setError(null);
    setIssues([]);
    const groups = parts ? [paths] : paths.map((p) => [p]);
    const collected: Issue[] = [];
    let ok = 0;
    try {
      for (const g of groups) {
        const out = await uploadOne({ paths: g, period: period || null });
        if (!out) continue;
        ok++;
        collected.push(...out.issues);
        const remembered = Object.entries(out.remembered);
        notifications.show({
          color: "teal",
          message:
            `Загружено: ${g.map(fileName).join(" + ")}, строк ${out.record.rows.toLocaleString("ru-RU")}` +
            (remembered.length ? `. Запомнены названия: ${remembered.map(([f, c]) => `«${f}» → ${c}`).join(", ")}` : ""),
        });
      }
      if (ok === groups.length) setText("");
      setIssues(collected);
      onDone?.();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const q = pending?.question;
  return (
    <Stack>
      <Textarea
        label="Файлы выгрузки"
        description="Путь к файлу на этом компьютере, по одному в строке. Несколько файлов — несколько загрузок по порядку."
        placeholder={"C:\\Выгрузки\\Продажи_2026-03.csv"}
        autosize
        minRows={2}
        value={text}
        onChange={(e) => setText(e.currentTarget.value)}
      />
      <Group align="flex-end">
        {splitPaths(text).length > 1 && (
          <Checkbox label="Это части одной выгрузки (склеить в одну загрузку)" checked={parts} onChange={(e) => setParts(e.currentTarget.checked)} />
        )}
        <TextInput
          label="Период"
          description={source.period_from === "upload" ? "Если его нет в имени файла" : "Обычно не нужен: берётся из дат выгрузки"}
          placeholder="2026-03"
          w={280}
          value={period}
          onChange={(e) => setPeriod(e.currentTarget.value.trim())}
        />
        <Button onClick={start} loading={busy} disabled={!splitPaths(text).length}>
          Загрузить
        </Button>
      </Group>
      <JobProgress jobId={jobId} />
      <ErrorAlert error={error} />
      <Issues issues={issues} />

      <Modal opened={q?.kind === "mapping"} onClose={() => answer(null)} title="Сопоставление столбцов" size="xl">
        {q?.kind === "mapping" && pending && (
          <MappingReview
            source={source}
            files={q.files}
            onCancel={() => answer(null)}
            onSubmit={(d: MappingDecision) =>
              answer({
                ...pending.body,
                mapping: { ...(pending.body.mapping ?? {}), ...d.mapping },
                declined: [...new Set([...(pending.body.declined ?? []), ...d.declined])],
              })
            }
          />
        )}
      </Modal>

      <Modal opened={q?.kind === "overlap"} onClose={() => answer(null)} title="Период уже загружен">
        {q?.kind === "overlap" && pending && (
          <Stack>
            <Text size="sm">{q.message}</Text>
            <Radio.Group value={policy} onChange={setPolicy}>
              <Stack gap="xs">
                {Object.entries(OVERLAP)
                  .filter(([k]) => k !== "ask")
                  .map(([k, v]) => (
                    <Radio key={k} value={k} label={v.label} description={v.hint} disabled={k === "merge_dedupe" && !source.keys?.length} />
                  ))}
              </Stack>
            </Radio.Group>
            <Group justify="flex-end">
              <Button variant="default" onClick={() => answer(null)}>
                Отменить
              </Button>
              <Button onClick={() => answer({ ...pending.body, overlap_policy: policy as UploadIn["overlap_policy"] })}>Загрузить</Button>
            </Group>
          </Stack>
        )}
      </Modal>

      <Modal opened={q?.kind === "again"} onClose={() => answer(null)} title="Файл уже загружен">
        {q?.kind === "again" && pending && (
          <Stack>
            <Text size="sm">{q.message}</Text>
            <Group justify="flex-end">
              <Button variant="default" onClick={() => answer(null)}>
                Не загружать
              </Button>
              <Button onClick={() => answer({ ...pending.body, force: true })}>Загрузить ещё раз</Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}
