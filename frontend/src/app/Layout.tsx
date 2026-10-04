import { AppShell, Badge, Box, Button, Center, Group, Loader, NavLink, Popover, Stack, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { IconDatabase, IconFileAnalytics, IconPalette, IconPlayerPlay, IconPuzzle } from "@tabler/icons-react";
import { useQuery, type QueryClient } from "@tanstack/react-query";
import { useShallow } from "zustand/react/shallow";
import { Suspense, lazy, useState, type ComponentType, type ReactNode } from "react";
import { NavLink as RouterLink, Navigate, Route, Routes, useLocation } from "react-router";
import { ApiError, api, connection, unwrap } from "../shared/api/client";
import { useConnection, useServerEvents } from "../shared/api/events";
import { isFinished, useJobs } from "../shared/api/jobs";
import { ErrorAlert } from "../shared/components/ErrorAlert";
import { JobProgress } from "../shared/components/JobProgress";
import { ErrorBoundary } from "./ErrorBoundary";

// Разделы загружаются лениво: каждый — отдельный кусок сборки со своей границей ошибок.
const SourcesPage = lazy(() => import("../features/sources/SourcesPage"));
const SourcePage = lazy(() => import("../features/sources/SourcePage"));
const ScenariosPage = lazy(() => import("../features/scenarios/ScenariosPage"));
const ScenarioPage = lazy(() => import("../features/scenarios/ScenarioPage"));
const ThemesPage = lazy(() => import("../features/themes/ThemesPage"));
const ThemePage = lazy(() => import("../features/themes/ThemePage"));
const RunsPage = lazy(() => import("../features/runs/RunsPage"));
const ModulesPage = lazy(() => import("../features/modules/ModulesPage"));

const SECTIONS = [
  { to: "/sources", label: "Источники", icon: IconDatabase },
  { to: "/scenarios", label: "Сценарии", icon: IconFileAnalytics },
  { to: "/themes", label: "Оформление", icon: IconPalette },
  { to: "/runs", label: "Запуски", icon: IconPlayerPlay },
  { to: "/modules", label: "Модули", icon: IconPuzzle },
];

function section(name: string, Page: ComponentType): ReactNode {
  return (
    <ErrorBoundary name={name}>
      <Suspense fallback={<Loader m="xl" />}>
        <Page />
      </Suspense>
    </ErrorBoundary>
  );
}

function TokenScreen() {
  const [token, setToken] = useState("");
  return (
    <Center h="100vh">
      <Stack w={420}>
        <Title order={3}>Нужен токен приложения</Title>
        <Text size="sm" c="dimmed">
          Окно открыто без токена. Его печатает команда <code>agen serve</code> при запуске (строка «Токен: …»), а
          ещё он лежит в файле cli.token в папке данных. Проще всего открыть окно командой <code>agen serve --open</code>.
        </Text>
        <TextInput label="Токен" value={token} onChange={(e) => setToken(e.currentTarget.value.trim())} />
        <Button
          disabled={!token}
          onClick={() => {
            connection.setToken(token);
            window.location.reload();
          }}
        >
          Войти
        </Button>
      </Stack>
    </Center>
  );
}

function ActiveJobs() {
  // useShallow: список собирается заново при каждом чтении, а перерисовка нужна, только если он изменился
  const jobs = useJobs(useShallow((s) => Object.values(s.jobs).filter((j) => !isFinished(j))));
  if (!jobs.length) return null;
  return (
    <Popover width={360} position="bottom-end" shadow="md">
      <Popover.Target>
        <Button size="xs" variant="light">
          Задания: {jobs.length}
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="sm">
          {jobs.map((j) => (
            <Box key={j.id}>
              <Text size="sm" fw={500}>
                {j.title || j.kind}
              </Text>
              <JobProgress jobId={j.id} />
            </Box>
          ))}
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

export function Layout({ queryClient }: { queryClient: QueryClient }) {
  useServerEvents(queryClient);
  const online = useConnection((s) => s.online);
  const location = useLocation();
  const system = useQuery({ queryKey: ["system"], queryFn: () => unwrap(api.GET("/api/system")), refetchInterval: 30_000 });

  if (system.error instanceof ApiError && system.error.status === 401) return <TokenScreen />;

  return (
    <AppShell header={{ height: 52 }} navbar={{ width: 210, breakpoint: "sm" }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="xs">
            <Title order={4}>Autogenerator</Title>
            {system.data && (
              <Text size="xs" c="dimmed">
                {system.data.version}
              </Text>
            )}
          </Group>
          <Group gap="sm">
            <ActiveJobs />
            <Tooltip label={online ? "Связь с сервером приложения есть" : "Нет связи с сервером приложения: переподключаюсь"}>
              <Badge color={online ? "teal" : "red"} variant="dot">
                {online ? "на связи" : "нет связи"}
              </Badge>
            </Tooltip>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="xs">
        {SECTIONS.map((s) => (
          <NavLink
            key={s.to}
            component={RouterLink}
            to={s.to}
            label={s.label}
            leftSection={<s.icon size={18} />}
            active={location.pathname.startsWith(s.to)}
          />
        ))}
        {system.data && (
          <Text size="xs" c="dimmed" mt="auto" p="xs" style={{ wordBreak: "break-all" }}>
            Папка данных: {system.data.home}
          </Text>
        )}
      </AppShell.Navbar>
      <AppShell.Main>
        {system.error && !(system.error instanceof ApiError && system.error.status === 401) && (
          <Box mb="md">
            <ErrorAlert error={system.error} />
          </Box>
        )}
        <Routes>
          <Route path="/" element={<Navigate to="/sources" replace />} />
          <Route path="/sources" element={section("Источники", SourcesPage)} />
          <Route path="/sources/:id" element={section("Источник", SourcePage)} />
          <Route path="/scenarios" element={section("Сценарии", ScenariosPage)} />
          <Route path="/scenarios/:id" element={section("Сценарий", ScenarioPage)} />
          <Route path="/themes" element={section("Оформление", ThemesPage)} />
          <Route path="/themes/:id" element={section("Шаблон", ThemePage)} />
          <Route path="/runs" element={section("Запуски", RunsPage)} />
          <Route path="/modules" element={section("Модули", ModulesPage)} />
          <Route path="*" element={<Navigate to="/sources" replace />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}
