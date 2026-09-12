import { Fragment, isValidElement, useMemo, useState, type ReactNode } from "react";
import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState
} from "@tanstack/react-table";
import { ChevronDown, ChevronLeft, ChevronRight, ChevronsUpDown, ChevronUp, Search } from "lucide-react";

export function DataTable<TData>({
  data,
  columns,
  searchPlaceholder = "Search resources",
  pageSize = 25,
  serverPage = false
}: {
  data: TData[];
  columns: Array<ColumnDef<TData, unknown>>;
  searchPlaceholder?: string;
  pageSize?: number;
  serverPage?: boolean;
}) {
  const safeData = Array.isArray(data) ? data : [];
  const [query, setQuery] = useState("");
  const [sorting, setSorting] = useState<SortingState>([]);
  const filteredData = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return safeData;
    return safeData.filter((row) => safeJson(row).toLowerCase().includes(normalized));
  }, [query, safeData]);
  const safeColumns = useMemo(() => normalizeColumns(columns), [columns]);
  const table = useReactTable({
    data: filteredData,
    columns: safeColumns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel()
    ,
    getSortedRowModel: getSortedRowModel(),
    manualPagination: serverPage,
    enableSorting: !serverPage,
    getPaginationRowModel: getPaginationRowModel(),
    initialState: {
      pagination: {
        pageIndex: 0,
        pageSize
      }
    }
  });
  const showSearch = !serverPage && safeData.length > 8;
  const pageCount = table.getPageCount();
  const pageIndex = table.getState().pagination.pageIndex;

  return (
    <div className="w-full min-w-0 max-w-full overflow-hidden rounded-xl border border-line bg-white">
      {showSearch && (
        <div className="flex flex-col gap-3 border-b border-line bg-slate-50/60 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
          <label className="relative block w-full sm:max-w-xs">
            <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={15} aria-hidden="true" />
            <span className="sr-only">{searchPlaceholder}</span>
            <input
              className="input h-9 bg-white pl-9"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                table.setPageIndex(0);
              }}
              placeholder={searchPlaceholder}
              type="search"
            />
          </label>
          <div className="text-xs font-medium text-muted" aria-live="polite">
            {filteredData.length.toLocaleString()} resource{filteredData.length === 1 ? "" : "s"}
          </div>
        </div>
      )}

      <div className="w-full min-w-0 max-w-full overflow-x-auto">
        <table className="min-w-full border-collapse text-left text-sm">
          <thead className="bg-slate-50/80 text-xs font-semibold text-muted">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  const canSort = header.column.getCanSort();
                  const sorted = header.column.getIsSorted();
                  return (
                    <th key={header.id} className="whitespace-nowrap border-b border-line px-3 py-2.5">
                      {header.isPlaceholder ? null : canSort ? (
                        <button
                          className="-mx-1 inline-flex items-center gap-1.5 rounded px-1 py-0.5 hover:text-ink"
                          onClick={header.column.getToggleSortingHandler()}
                          type="button"
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {sorted === "asc" ? <ChevronUp size={13} /> : sorted === "desc" ? <ChevronDown size={13} /> : <ChevronsUpDown size={13} className="opacity-50" />}
                        </button>
                      ) : (
                        flexRender(header.column.columnDef.header, header.getContext())
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-line bg-white">
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} className="transition-colors hover:bg-blue-50/35">
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-3 align-middle text-slate-700">
                    {safeRender(flexRender(cell.column.columnDef.cell, cell.getContext()))}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!serverPage && pageCount > 1 && (
        <div className="flex items-center justify-between border-t border-line bg-slate-50/50 px-3 py-2.5">
          <div className="text-xs font-medium text-muted">
            Page {pageIndex + 1} of {pageCount}
          </div>
          <div className="flex items-center gap-1">
            <button
              className="btn h-8 w-8 p-0"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              aria-label="Previous page"
              title="Previous page"
              type="button"
            >
              <ChevronLeft size={15} />
            </button>
            <button
              className="btn h-8 w-8 p-0"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              aria-label="Next page"
              title="Next page"
              type="button"
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function normalizeColumns<TData>(columns: Array<ColumnDef<TData, unknown>>): Array<ColumnDef<TData, unknown>> {
  return columns.map((column, index) => {
    const header = (column as { header?: unknown }).header;
    const accessorKey = (column as { accessorKey?: unknown }).accessorKey;
    const explicitId = (column as { id?: unknown }).id;
    const id =
      typeof explicitId === "string" && explicitId.trim()
        ? explicitId
        : typeof accessorKey === "string" && accessorKey.trim()
          ? accessorKey.replace(/\./g, "_")
          : typeof header === "string" && header.trim()
            ? `${header}-${index}`
            : `column-${index}`;

    return { ...column, id };
  });
}

function safeRender(value: unknown): ReactNode {
  if (value === null || value === undefined || typeof value === "boolean") return value as ReactNode;
  if (typeof value === "string" || typeof value === "number") return value;
  if (isValidElement(value)) return value;
  if (Array.isArray(value)) {
    return value.map((item, index) => <Fragment key={index}>{safeRender(item)}</Fragment>);
  }
  if (typeof value === "object") {
    return <code className="break-all font-mono text-xs">{safeJson(value)}</code>;
  }
  return String(value);
}

function safeJson(value: unknown) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
