import { useMemo } from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import type { Directive, Filters, Flag } from "../types";
import { FlagStrip } from "./FlagBadge";

const column = createColumnHelper<Directive>();

/** The severity column is 44px wide; truncation alone produces "MEDI"/"UNKN". */
const SEVERITY_ABBREV: Record<string, string> = {
  CRITICAL: "CRIT",
  HIGH: "HIGH",
  MEDIUM: "MED",
  LOW: "LOW",
  UNKNOWN: "UNK",
};

function allFlags(directive: Directive): Flag[] {
  return [...directive.flags, ...directive.action_items.flatMap((item) => item.flags)];
}

function initials(name: string): string {
  const parts = name.replace(/[^\p{L}\s.]/gu, "").split(/[\s.]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/**
 * Who is accountable, without widening the row. One name reads in full; more
 * than one collapses to initials so the column stays a fixed width.
 */
function OwnerCell({ directive }: { directive: Directive }) {
  const names = [
    ...new Set(
      directive.action_items
        .map((item) => item.owner)
        .filter((owner): owner is string => Boolean(owner)),
    ),
  ];
  const unowned = directive.action_items.some((item) => !item.owner);

  if (names.length === 0) {
    return (
      <span className="missing" title="No action item has an accountable owner">
        {directive.action_items.length === 0 ? "—" : "unassigned"}
      </span>
    );
  }

  if (names.length === 1) {
    return (
      <span className="owner-cell" title={unowned ? `${names[0]} (+ unassigned work)` : names[0]}>
        <span className="avatar sm" aria-hidden="true">
          {initials(names[0])}
        </span>
        <span className="owner-name">{names[0]}</span>
        {unowned && <span className="owner-more">+?</span>}
      </span>
    );
  }

  return (
    <span className="owner-cell" title={names.join(", ")}>
      {names.slice(0, 3).map((name) => (
        <span className="avatar sm" key={name} aria-hidden="true">
          {initials(name)}
        </span>
      ))}
      {names.length > 3 && <span className="owner-more">+{names.length - 3}</span>}
    </span>
  );
}

function DeadlineCell({ directive }: { directive: Directive }) {
  if (!directive.deadline) {
    const raw = directive.raw_dates.deadline;
    return (
      <span className="missing" title={raw ? `Feed sent: ${raw}` : "No deadline supplied"}>
        {raw ? "unparsed" : "none"}
      </span>
    );
  }
  const days = directive.days_to_deadline ?? 0;
  const open = directive.open_item_count > 0;
  const className = days < 0 && open ? "overdue" : days <= 30 && open ? "due-soon" : undefined;
  return (
    <span className={className} title={days < 0 ? `${-days} days overdue` : `in ${days} days`}>
      {directive.deadline}
    </span>
  );
}

export function DirectiveTable({
  rows,
  filters,
  selectedId,
  onSelectRow,
  onSort,
}: {
  rows: Directive[];
  filters: Filters;
  selectedId: number | null;
  onSelectRow: (id: number) => void;
  onSort: (key: Filters["sort"]) => void;
}) {
  const columns = useMemo(
    () => [
      column.display({
        id: "expand",
        header: () => "",
        // A right chevron: clicking the row opens its detail in the side panel.
        cell: () => "›",
      }),
      column.accessor("id", {
        id: "sno",
        header: () => "ID",
        // The directive's own stable id, so the number stays fixed no matter how
        // the result set is filtered, sorted, or paged.
        cell: (ctx) => ctx.getValue(),
        meta: { sort: "number" as const },
      }),
      column.accessor("reference_code", {
        header: () => "Reference",
        cell: (ctx) =>
          ctx.getValue() ?? <span className="missing">no reference</span>,
        meta: { sort: "reference" as const },
      }),
      column.accessor((row) => row.authority.code, {
        id: "authority",
        header: () => "Auth",
        cell: (ctx) => <span className="tag auth-tag">{ctx.getValue()}</span>,
        meta: { sort: "authority" as const },
      }),
      column.accessor("severity", {
        header: () => "Sev",
        cell: (ctx) => (
          <span className={`tag sev-${ctx.getValue()}`} title={ctx.getValue()}>
            {SEVERITY_ABBREV[ctx.getValue()] ?? ctx.getValue()}
          </span>
        ),
        meta: { sort: "severity" as const },
      }),
      column.accessor("status", {
        header: () => "Status",
        cell: (ctx) => (
          <span
            className={`st st-${ctx.getValue()}`}
            title={
              ctx.row.original.raw_status && ctx.row.original.raw_status !== ctx.getValue()
                ? `Feed sent: ${ctx.row.original.raw_status}`
                : undefined
            }
          >
            {ctx.getValue()}
          </span>
        ),
      }),
      column.accessor("title", {
        header: () => "Title",
        cell: (ctx) =>
          ctx.getValue() ?? <span className="missing">title could not be extracted</span>,
      }),
      column.display({
        id: "owners",
        header: () => "Owners",
        cell: (ctx) => <OwnerCell directive={ctx.row.original} />,
      }),
      column.accessor("deadline", {
        header: () => "Deadline",
        cell: (ctx) => <DeadlineCell directive={ctx.row.original} />,
        meta: { sort: "deadline" as const },
      }),
      column.display({
        id: "items",
        header: () => "Open",
        cell: (ctx) =>
          ctx.row.original.total_item_count === 0 ? (
            <span className="missing">none</span>
          ) : (
            `${ctx.row.original.open_item_count}/${ctx.row.original.total_item_count}`
          ),
      }),
      column.display({
        id: "flags",
        header: () => "Flags",
        cell: (ctx) => <FlagStrip flags={allFlags(ctx.row.original)} />,
      }),
    ],
    [],
  );

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => String(row.id),
  });

  const cellClass: Record<string, string> = {
    expand: "c-expand",
    sno: "c-sno",
    reference_code: "c-ref",
    authority: "c-auth",
    severity: "c-sev",
    status: "c-status",
    title: "c-title",
    owners: "c-owners",
    deadline: "c-date",
    items: "c-items",
    flags: "c-flags",
  };

  return (
    <table className="grid">
      <thead>
        {table.getHeaderGroups().map((group) => (
          <tr key={group.id}>
            {group.headers.map((header) => {
              const sortKey = (header.column.columnDef.meta as { sort?: Filters["sort"] } | undefined)
                ?.sort;
              const active = sortKey === filters.sort;
              return (
                <th
                  key={header.id}
                  className={sortKey ? "sortable" : undefined}
                  onClick={sortKey ? () => onSort(sortKey) : undefined}
                  title={sortKey ? "Sort by this column" : undefined}
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                  {active ? (filters.order === "asc" ? " ↑" : " ↓") : ""}
                </th>
              );
            })}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => {
          const directive = row.original;
          const isSelected = directive.id === selectedId;
          return (
            <tr
              key={row.id}
              className={`row ${isSelected ? "selected" : ""}`}
              onClick={() => onSelectRow(directive.id)}
            >
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className={cellClass[cell.column.id] ?? ""}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
