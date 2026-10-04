import { MantineProvider, createTheme } from "@mantine/core";
import { ModalsProvider } from "@mantine/modals";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HashRouter } from "react-router";
import { ApiError } from "../shared/api/client";
import { Layout } from "./Layout";

const theme = createTheme({
  primaryColor: "indigo",
  defaultRadius: "md",
  fontFamily: "Segoe UI, system-ui, -apple-system, Roboto, sans-serif",
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      // 404 и ошибки доступа не повторяются: повтор их не исправит
      retry: (n, e) => !(e instanceof ApiError && [401, 404, 422].includes(e.status)) && n < 2,
    },
  },
});

export function App() {
  return (
    <MantineProvider theme={theme} defaultColorScheme="light">
      <Notifications position="bottom-right" />
      <QueryClientProvider client={queryClient}>
        <ModalsProvider>
          <HashRouter>
            <Layout queryClient={queryClient} />
          </HashRouter>
        </ModalsProvider>
      </QueryClientProvider>
    </MantineProvider>
  );
}
