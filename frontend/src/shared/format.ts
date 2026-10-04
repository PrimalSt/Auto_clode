// Числа, даты и периоды так, как их читает человек.
import type { Period } from "./api/client";

const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];

function parseDate(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

function dmy(d: Date): string {
  return `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")}.${d.getUTCFullYear()}`;
}

/** «март 2026», «1 кв. 2026», «2026», «15.03.2026», «неделя 12, 2026», «03.03.2026 – 19.03.2026». */
export function periodLabel(p: Period | null | undefined): string {
  if (!p) return "—";
  const start = parseDate(p.start);
  const last = new Date(parseDate(p.end_exclusive).getTime() - 86_400_000);
  const y = start.getUTCFullYear();
  switch (p.unit) {
    case "year":
      return String(y);
    case "quarter":
      return `${Math.floor(start.getUTCMonth() / 3) + 1} кв. ${y}`;
    case "month":
      return `${MONTHS[start.getUTCMonth()]} ${y}`;
    case "day":
      return dmy(start);
    case "week":
      return `${dmy(start)} – ${dmy(last)}`;
    default:
      return `${dmy(start)} – ${dmy(last)}`;
  }
}

/** Запись периода для сервера, обратная разбору: 2026-03, 2026-Q1, 2026, 2026-03-03..2026-03-19. */
export function periodKey(p: Period): string {
  const start = parseDate(p.start);
  const y = start.getUTCFullYear();
  const mm = String(start.getUTCMonth() + 1).padStart(2, "0");
  switch (p.unit) {
    case "year":
      return String(y);
    case "quarter":
      return `${y}-Q${Math.floor(start.getUTCMonth() / 3) + 1}`;
    case "month":
      return `${y}-${mm}`;
    case "day":
      return p.start.slice(0, 10);
    default: {
      const last = new Date(parseDate(p.end_exclusive).getTime() - 86_400_000);
      return `${p.start.slice(0, 10)}..${last.toISOString().slice(0, 10)}`;
    }
  }
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function bytes(n: number | null | undefined): string {
  if (n == null) return "—";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: i === 0 ? 0 : 1 })} ${units[i]}`;
}

export function count(n: number | null | undefined): string {
  return n == null ? "—" : n.toLocaleString("ru-RU");
}

/** Склонение: plural(3, "строка", "строки", "строк") → «строки». */
export function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

export function percent(x: number): string {
  return `${Math.round(x * 100)}%`;
}

/** Имя файла из пути (Windows или POSIX). */
export function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}
