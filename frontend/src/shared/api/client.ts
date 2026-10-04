// Подключение к серверу приложения и типизированный клиент API (типы — из схемы OpenAPI сервера:
// `npm run api` после правки разделов API).
import createClient, { type Middleware } from "openapi-fetch";
import type { Readable } from "openapi-typescript-helpers";
import type { components, paths } from "./schema";

/** Модель ответа сервера в том виде, в каком её отдаёт клиент API (openapi-fetch). */
export type Schemas = { [K in keyof components["schemas"]]: Readable<components["schemas"][K]> };
export type SourceRecord = Schemas["SourceRecord"];
export type SourceSpec = Schemas["SourceSpec-Output"];
export type ColumnSpec = Schemas["ColumnSpec-Output"];
export type UploadRecord = Schemas["UploadRecord"];
export type ScenarioRecord = Schemas["ScenarioRecord"];
export type ThemeRecord = Schemas["ThemeRecord"];
export type ThemeManifest = Schemas["ThemeManifest"];
export type RunRecord = Schemas["RunRecord"];
export type JobInfo = Schemas["JobInfo"];
export type Issue = Schemas["Issue"];
export type Period = Schemas["Period"];
export type ReconcileResult = Schemas["ReconcileResult"];
export type MappingCandidate = Schemas["MappingCandidate"];
export type PreviewResult = Schemas["PreviewResult"];

/** То, что окну передаёт оболочка (Tauri, часть 4): адрес сервера, токен и мост к системе. */
export interface ShellBridge {
  url?: string;
  token?: string;
  /** Нативный выбор файлов: пути к файлам на этом компьютере или null (отмена). */
  pickFiles?: (opts: { title?: string; multiple?: boolean; extensions?: string[] }) => Promise<string[] | null>;
  /** Нативный выбор папки. */
  pickFolder?: (opts: { title?: string }) => Promise<string | null>;
  /** Открыть файл программой по умолчанию (готовый отчёт — в PowerPoint). */
  openPath?: (path: string) => Promise<void>;
}

declare global {
  interface Window {
    __AGEN__?: ShellBridge;
  }
}

const TOKEN_KEY = "agen-token";

function session(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** Токен из адреса (`agen serve --open` открывает окно с `#token=…`): запомнить и убрать из адреса. */
export function takeTokenFromUrl(): void {
  const m = /^#token=([^&]+)/.exec(window.location.hash);
  if (!m) return;
  session()?.setItem(TOKEN_KEY, decodeURIComponent(m[1]));
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#/`);
}

export const connection = {
  get baseUrl(): string {
    return window.__AGEN__?.url ?? "";
  },
  get token(): string | null {
    return window.__AGEN__?.token ?? session()?.getItem(TOKEN_KEY) ?? null;
  },
  setToken(token: string): void {
    session()?.setItem(TOKEN_KEY, token);
  },
};

/** Ошибка сервера: код из ErrorCode, текст для человека, подсказка, подробности. */
export class ApiError extends Error {
  code: string;
  hint: string | null;
  details: Record<string, unknown>;
  status: number;

  constructor(code: string, message: string, hint: string | null = null, details: Record<string, unknown> = {}, status = 0) {
    super(message);
    this.code = code;
    this.hint = hint;
    this.details = details;
    this.status = status;
  }
}

function toApiError(error: unknown, response: Response | undefined): ApiError {
  const e = (error ?? {}) as { code?: string; message?: string; hint?: string | null; details?: Record<string, unknown> };
  const status = response?.status ?? 0;
  if (status === 401) return new ApiError("unauthorized", "Нет доступа к серверу приложения: неверный токен", null, {}, 401);
  return new ApiError(e.code ?? "http_" + status, e.message ?? `Сервер ответил ${status}`, e.hint ?? null, e.details ?? {}, status);
}

const auth: Middleware = {
  onRequest({ request }) {
    const token = connection.token;
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: connection.baseUrl });
api.use(auth);

/** Данные ответа или ApiError. */
export async function unwrap<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  let res: { data?: T; error?: unknown; response: Response };
  try {
    res = await p;
  } catch (e) {
    throw new ApiError("offline", "Сервер приложения не отвечает", "Если окно открыто из браузера, проверьте, что работает agen serve.", { cause: String(e) });
  }
  if (res.error !== undefined || !res.response.ok) throw toApiError(res.error, res.response);
  return res.data as T;
}

/** Адрес для `<img src>` и скачивания: токен — параметром запроса (только для GET). */
export function withToken(path: string): string {
  const token = connection.token;
  const sep = path.includes("?") ? "&" : "?";
  return `${connection.baseUrl}${path}${token ? `${sep}token=${encodeURIComponent(token)}` : ""}`;
}
