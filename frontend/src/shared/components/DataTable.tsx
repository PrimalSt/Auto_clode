// Таблица строк превью: виртуальная прокрутка AG Grid, числа — по-русски и по правому краю.
import { AllCommunityModule, ModuleRegistry, type ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useMemo } from "react";

ModuleRegistry.registerModules([AllCommunityModule]);

const number = (v: unknown) => (typeof v === "number" ? v.toLocaleString("ru-RU", { maximumFractionDigits: Math.abs(v) < 1 ? 4 : 2 }) : v == null ? "" : String(v));

export function DataTable({ columns, rows, height = 420 }: { columns: string[]; rows: Record<string, unknown>[]; height?: number }) {
  const defs = useMemo<ColDef[]>(
    () =>
      columns.map((c) => {
        const numeric = rows.some((r) => typeof r[c] === "number");
        return {
          field: c,
          headerName: c,
          sortable: true,
          resizable: true,
          filter: true,
          ...(numeric ? { type: "rightAligned", valueFormatter: (p: { value: unknown }) => number(p.value) } : {}),
        };
      }),
    [columns, rows],
  );
  return (
    <div style={{ height, width: "100%" }}>
      <AgGridReact rowData={rows} columnDefs={defs} defaultColDef={{ minWidth: 90 }} autoSizeStrategy={{ type: "fitCellContents" }} />
    </div>
  );
}
