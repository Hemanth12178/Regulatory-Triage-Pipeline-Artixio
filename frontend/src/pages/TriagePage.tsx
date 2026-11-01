import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  fetchAuthorities,
  fetchDirectives,
  fetchOwners,
  fetchVocabulary,
  patchActionItem,
} from "../lib/api";
import {
  DEFAULT_FILTERS,
  EMPTY_VOCABULARY,
  type ActionItem,
  type DirectivePage,
  type Filters,
} from "../types";
import { BrandMark } from "../components/BrandMark";
import { DirectiveDetail } from "../components/DirectiveDetail";
import { DirectiveTable } from "../components/DirectiveTable";
import { FilterBar } from "../components/FilterBar";
import { StatsStrip } from "../components/StatsStrip";
import { Toasts, useToasts } from "../components/Toast";

export default function TriagePage() {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [pendingItemIds, setPendingItemIds] = useState<Set<number>>(new Set());
  const { toasts, push, dismiss } = useToasts();
  const queryClient = useQueryClient();

  const queryKey = useMemo(() => ["directives", filters] as const, [filters]);

  const directives = useQuery({
    queryKey,
    queryFn: () => fetchDirectives(filters),
    // Keep the old page on screen while the next one loads: no flicker, and
    // the table never collapses to zero height mid-triage.
    placeholderData: keepPreviousData,
  });

  const authorities = useQuery({
    queryKey: ["authorities"],
    queryFn: fetchAuthorities,
    staleTime: 10 * 60 * 1000,
  });

  // Owner workloads shift as items are triaged, so this is refreshed with the
  // directive list rather than cached for the session like authorities are.
  const owners = useQuery({ queryKey: ["owners"], queryFn: fetchOwners });

  // Enums and the action item state machine come from the server, so the UI
  // cannot drift from the rules the API actually enforces.
  const vocabularyQuery = useQuery({
    queryKey: ["vocabulary"],
    queryFn: fetchVocabulary,
    staleTime: 30 * 60 * 1000,
  });
  const vocabulary = vocabularyQuery.data ?? EMPTY_VOCABULARY;

  const patchFilters = useCallback((patch: Partial<Filters>) => {
    setFilters((current) => ({ ...current, ...patch }));
  }, []);

  // Clicking the open row again closes the side panel; any other row switches
  // the panel to it.
  const selectRow = useCallback((id: number) => {
    setSelectedId((current) => (current === id ? null : id));
  }, []);

  // Escape closes the panel, matching the × button.
  useEffect(() => {
    if (selectedId === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectedId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId]);

  const onSort = useCallback((key: Filters["sort"]) => {
    setFilters((current) => ({
      ...current,
      sort: key,
      order: current.sort === key && current.order === "asc" ? "desc" : "asc",
      page: 1,
    }));
  }, []);

  const mutation = useMutation({
    mutationFn: (vars: { item: ActionItem; next: string; notes?: string }) =>
      patchActionItem(vars.item.id, {
        status: vars.next,
        ...(vars.notes ? { notes: vars.notes } : {}),
        // Optimistic concurrency: the server rejects the write if this row
        // changed since we rendered it.
        expected_updated_at: vars.item.updated_at,
      }),

    onMutate: async (vars) => {
      await queryClient.cancelQueries({ queryKey });
      const snapshot = queryClient.getQueryData<DirectivePage>(queryKey);
      setPendingItemIds((current) => new Set(current).add(vars.item.id));

      // Optimistic write: flip the status locally so the UI stays responsive.
      queryClient.setQueryData<DirectivePage>(queryKey, (page) =>
        page
          ? {
              ...page,
              items: page.items.map((directive) => {
                const action_items = directive.action_items.map((item) =>
                  item.id === vars.item.id
                    ? {
                        ...item,
                        status: vars.next,
                        is_open: vars.next !== "RESOLVED" && vars.next !== "DISMISSED",
                      }
                    : item,
                );
                return {
                  ...directive,
                  action_items,
                  // Derive the counter too: leaving it stale made the OPEN
                  // column disagree with the row it summarises until the
                  // refetch landed.
                  open_item_count: action_items.filter((item) => item.is_open).length,
                };
              }),
            }
          : page,
      );

      return { snapshot };
    },

    onError: (error, vars, context) => {
      // Roll back to exactly what the server last told us.
      if (context?.snapshot) queryClient.setQueryData(queryKey, context.snapshot);
      const message =
        error instanceof ApiError ? error.message : "Unexpected error updating the action item.";
      push("error", `#${vars.item.id}: ${message}`);
    },

    onSuccess: (updated) => {
      push("success", `#${updated.id} → ${updated.status}`);
    },

    onSettled: (_data, _error, vars) => {
      setPendingItemIds((current) => {
        const next = new Set(current);
        next.delete(vars.item.id);
        return next;
      });
      // Re-fetch so derived values (flags, counts, roll-ups) come from the
      // server rather than from our optimistic guess. Owner workloads move
      // with every status change, so they are refreshed too.
      queryClient.invalidateQueries({ queryKey: ["directives"] });
      queryClient.invalidateQueries({ queryKey: ["owners"] });
    },
  });

  const onChangeStatus = useCallback(
    (item: ActionItem, next: string) => {
      if (next === item.status) return;

      // The API requires a justification before dismissing. Ask for it here
      // instead of letting the user discover the rule through a 422.
      let notes: string | undefined;
      if (next === "DISMISSED" && !(item.notes ?? "").trim()) {
        const entered = window.prompt("Dismissing this item requires a justification note:", "");
        if (entered === null) return;
        if (!entered.trim()) {
          push("error", "A justification note is required to dismiss an item.");
          return;
        }
        notes = entered.trim();
      }

      mutation.mutate({ item, next, notes });
    },
    [mutation, push],
  );

  const page = directives.data;
  const rows = page?.items ?? [];

  // The panel always renders the freshest copy of the row from the query
  // cache, so a status change inside it reflects immediately. If the row drops
  // out of the current page/filter, the panel closes on its own.
  const selectedDirective = selectedId === null ? null : rows.find((r) => r.id === selectedId) ?? null;

  return (
    <div className="app">
      <header className="topbar">
        {/* Top-left escape hatch back to the overview. */}
        <Link to="/" className="back-btn" title="Back to home" aria-label="Back to home">
          ←
        </Link>

        <Link to="/" className="brand" title="Back to home">
          <BrandMark className="brand-mark" />
          <span className="brand-text">
            <span className="brand-name">ARTIXIO</span>
            <span className="brand-product">Regulatory Intelligence Triage</span>
          </span>
        </Link>

        <span className="count-pill" title="Directives matching the current filters">
          {page ? page.total : "—"}
          <small>matching</small>
        </span>
        {directives.isFetching && page ? <span className="sub">refreshing…</span> : null}

        <span className="spacer" />
        <span className="legend-hint">Click any row for its full audit trail</span>
      </header>

      {page && <StatsStrip stats={page.stats} filters={filters} onFilters={patchFilters} />}

      <FilterBar
        filters={filters}
        authorities={authorities.data ?? []}
        owners={owners.data ?? []}
        vocabulary={vocabulary}
        onFilters={patchFilters}
        onReset={() => setFilters(DEFAULT_FILTERS)}
      />

      <div className="table-wrap">
        {directives.isFetching && <div className="busy" />}

        {directives.isError ? (
          <div className="placeholder flagged">
            <p>{(directives.error as ApiError).message}</p>
            <p>
              Start it with <code>uvicorn app.main:app --reload --port 8000</code> in{" "}
              <code>backend/</code>.
            </p>
          </div>
        ) : directives.isLoading ? (
          <div className="placeholder">
            <span className="spinner" aria-hidden="true" />
            <p>Loading directives…</p>
          </div>
        ) : rows.length === 0 ? (
          <div className="placeholder">
            <svg className="placeholder-mark" viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="11" cy="11" r="6.5" />
              <path d="m16 16 4.5 4.5" />
            </svg>
            <p className="placeholder-title">No directive matches these filters</p>
            <p>Try widening the search, or clear the filters to start again.</p>
            <button className="btn-soft" onClick={() => setFilters(DEFAULT_FILTERS)}>
              Clear all filters
            </button>
          </div>
        ) : (
          <DirectiveTable
            rows={rows}
            filters={filters}
            selectedId={selectedId}
            onSelectRow={selectRow}
            onSort={onSort}
          />
        )}
      </div>

      {selectedDirective && (
        <div
          className="detail-overlay"
          role="presentation"
          onClick={() => setSelectedId(null)}
        >
          <aside
            className="detail-panel"
            role="dialog"
            aria-modal="true"
            aria-label="Directive detail"
            onClick={(event) => event.stopPropagation()}
          >
            <DirectiveDetail
              directive={selectedDirective}
              pendingItemIds={pendingItemIds}
              vocabulary={vocabulary}
              onChangeStatus={onChangeStatus}
              onClose={() => setSelectedId(null)}
            />
          </aside>
        </div>
      )}

      <footer className="footer">
        <span>
          {page ? `Page ${page.page} of ${page.pages} · ${rows.length} entries` : "—"}
        </span>
        <span className="spacer" />
        <span className="pager">
          <button
            disabled={!page || page.page <= 1}
            onClick={() => patchFilters({ page: Math.max(1, (page?.page ?? 1) - 1) })}
          >
            ‹ Prev
          </button>
          <button
            disabled={!page || page.page >= page.pages}
            onClick={() => patchFilters({ page: (page?.page ?? 1) + 1 })}
          >
            Next ›
          </button>
        </span>
      </footer>

      <Toasts toasts={toasts} onDismiss={dismiss} />
    </div>
  );
}
