import { Button, Stack } from "@mantine/core";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { ErrorAlert } from "../shared/components/ErrorAlert";

interface State {
  error: Error | null;
}

/** Граница ошибок раздела: сбой в одном разделе не ломает остальные (ARCHITECTURE.md, раздел 11). */
export class ErrorBoundary extends Component<{ children: ReactNode; name: string }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`Раздел «${this.props.name}»`, error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <Stack maw={720}>
        <ErrorAlert title={`Раздел «${this.props.name}» не открылся`} error={this.state.error} />
        <Button variant="default" w="fit-content" onClick={() => this.setState({ error: null })}>
          Попробовать снова
        </Button>
      </Stack>
    );
  }
}
