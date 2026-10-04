import { Select } from "@mantine/core";
import { useState } from "react";
import { Page } from "../../shared/components/Page";
import { useScenarios } from "../scenarios/queries";
import { RunsTable } from "./RunsTable";

export default function RunsPage() {
  const scenarios = useScenarios();
  const [scenario, setScenario] = useState<string | null>(null);
  return (
    <Page
      title="Запуски"
      subtitle="Собранные отчёты: период, итог, журнал; пересборка за прошлый период по текущей истории"
      actions={
        <Select
          placeholder="Все сценарии"
          data={(scenarios.data ?? []).map((s) => ({ value: s.id, label: s.name }))}
          value={scenario}
          onChange={setScenario}
          clearable
          w={260}
        />
      }
    >
      <RunsTable scenario={scenario ?? undefined} />
    </Page>
  );
}
