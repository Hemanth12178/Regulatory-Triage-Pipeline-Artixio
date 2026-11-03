import { useEffect, useRef, useState } from "react";
import {
  UNASSIGNED_OWNER,
  type Authority,
  type Filters,
  type Owner,
  type Quality,
  type Vocabulary,
} from "../types";

/** Must match `DirectiveStatus` in the backend's sanitize.py. */
const QUALITY_LABEL: Record<Quality, string> = {
  error: "Has errors",
  warning: "Has warnings",
  ok: "Clean",
};

/** Sentinel for "more than one value is active on this dimension". */
const MULTI = "__multi__";

/** Backend sentinel (routers/directives.py): "carries any flag at all". */
const ANY_FLAG = "ANY";

/** Dimensions that count towards the badge. Search is its own control. */
const FILTER_KEYS = [
  "authority",
  "status",
  "severity",
  "quality",
  "flag",
  "owner",
  "owner_status",
] as const;

/** "IN_PROGRESS" -> "In progress" for the owner-status dropdown and chips. */
function humaniseStatus(value: string): string {
  const spaced = value.replace(/_/g, " ").toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function activeFilterCount(filters: Filters): number {
  return (
    FILTER_KEYS.reduce((total, key) => total + filters[key].length, 0) +
    (filters.only_open ? 1 : 0)
  );
}

/**
 * Single-select control over a dimension the API treats as a list. When more
 * than one value is active (the stat strip can set several), the control says
 * so instead of falsely reading "All" - and picking a value then visibly
 * replaces the set rather than silently discarding part of it.
 */
function Field({
  label,
  values,
  options,
  placeholder,
  onChange,
}: {
  label: string;
  values: string[];
  options: Array<{ value: string; label: string }>;
  placeholder: string;
  onChange: (value: string) => void;
}) {
  const multiple = values.length > 1;
  const value = multiple ? MULTI : (values[0] ?? "");

  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <select
        className={`control ${multiple ? "is-multi" : ""}`}
        value={value}
        title={multiple ? values.join(", ") : undefined}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{placeholder}</option>
        {multiple && (
          <option value={MULTI}>{values.length} selected — pick one to replace</option>
        )}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function FilterBar({
  filters,
  authorities,
  owners,
  vocabulary,
  onFilters,
  onReset,
}: {
  filters: Filters;
  authorities: Authority[];
  owners: Owner[];
  vocabulary: Vocabulary;
  onFilters: (patch: Partial<Filters>) => void;
  onReset: () => void;
}) {
  const [draft, setDraft] = useState(filters.q);
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Debounce typing so every keystroke is not a request.
  useEffect(() => {
    const timer = setTimeout(() => {
      if (draft !== filters.q) onFilters({ q: draft, page: 1 });
    }, 220);
    return () => clearTimeout(timer);
  }, [draft, filters.q, onFilters]);

  useEffect(() => {
    setDraft(filters.q);
  }, [filters.q]);

  // "/" focuses search, Escape clears it or closes the panel.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
      if (event.key === "/" && !typing) {
        event.preventDefault();
        inputRef.current?.focus();
      }
      if (event.key === "Escape") {
        if (open) setOpen(false);
        else if (document.activeElement === inputRef.current) {
          setDraft("");
          inputRef.current?.blur();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Clicking anywhere outside the popover dismisses it.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!panelRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  // Choosing MULTI is a no-op: it only exists to describe the current state.
  const setOne = (key: keyof Filters, value: string) => {
    if (value === MULTI) return;
    onFilters({ [key]: value ? [value] : [], page: 1 } as Partial<Filters>);
  };

  const count = activeFilterCount(filters);

  const ownerLabel = (value: string) =>
    owners.find((o) => o.value === value)?.label ??
    (value === UNASSIGNED_OWNER ? "Unassigned" : value);

  /**
   * One chip per dimension, not per value. Several values on the same
   * dimension are OR-ed by the API, so "flag: A" + "flag: B" as two chips
   * reads like a narrowing AND when it actually widens the result.
   */
  const chipFor = (
    key: "authority" | "status" | "severity" | "owner" | "quality" | "flag" | "owner_status",
    caption: string,
    render: (value: string) => string = (v) => v,
  ) => {
    const values = filters[key];
    if (values.length === 0) return [];
    return [
      {
        label: `${caption}: ${values.map(render).join(" or ")}`,
        clear: () => onFilters({ [key]: [], page: 1 } as Partial<Filters>),
      },
    ];
  };

  const activeChips: Array<{ label: string; clear: () => void }> = [
    ...chipFor("authority", "authority"),
    ...chipFor("status", "status"),
    ...chipFor("severity", "severity"),
    ...chipFor("owner", "owner", ownerLabel),
    ...chipFor("owner_status", "owner status", humaniseStatus),
    ...chipFor("quality", "quality"),
    ...chipFor("flag", "flag", (code) =>
      code === ANY_FLAG ? "any flag" : code.toLowerCase().replace(/_/g, " "),
    ),
  ];

  if (filters.only_open) {
    activeChips.push({
      label: "open work only",
      clear: () => onFilters({ only_open: false, page: 1 }),
    });
  }

  return (
    <>
      <div className="filters">
        <div className="search">
          <span className="icon">⌕</span>
          <input
            ref={inputRef}
            value={draft}
            placeholder="Search reference, title, summary, owner…"
            onChange={(event) => setDraft(event.target.value)}
            aria-label="Search directives"
          />
          {draft === "" && <span className="hint">/</span>}
        </div>

        <div className="filter-menu" ref={panelRef}>
          <button
            className={`toggle filter-btn ${open || count > 0 ? "on" : ""}`}
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-haspopup="dialog"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" className="filter-icon">
              <path d="M3.5 5.5h17l-6.6 7.6v5.2l-3.8 2v-7.2L3.5 5.5Z" />
            </svg>
            Filters
            {count > 0 && <span className="filter-badge">{count}</span>}
          </button>

          {open && (
            <div className="filter-panel" role="dialog" aria-label="Filter directives">
              <div className="panel-head">
                <strong>Filters</strong>
                {count > 0 && (
                  <button className="btn-link" onClick={onReset}>
                    Clear all
                  </button>
                )}
              </div>

              <div className="panel-grid">
                <Field
                  label="Authority"
                  placeholder="All authorities"
                  values={filters.authority}
                  onChange={(value) => setOne("authority", value)}
                  options={authorities.map((a) => ({
                    value: a.code,
                    label: `${a.code} — ${a.country}`,
                  }))}
                />

                <Field
                  label="Owner"
                  placeholder="Anyone"
                  values={filters.owner}
                  onChange={(value) => setOne("owner", value)}
                  options={owners.map((o) => ({
                    value: o.value,
                    label: `${o.label} (${o.open_items} open)`,
                  }))}
                />

                <Field
                  label="Owner status"
                  placeholder="Any owner status"
                  values={filters.owner_status}
                  onChange={(value) => setOne("owner_status", value)}
                  // UNKNOWN is a corrupt/missing status, not a real owner state,
                  // so it is not offered as a filter choice.
                  options={vocabulary.action_status
                    .filter((s) => s !== "UNKNOWN")
                    .map((s) => ({ value: s, label: humaniseStatus(s) }))}
                />

                <Field
                  label="Directive status"
                  placeholder="All statuses"
                  values={filters.status}
                  onChange={(value) => setOne("status", value)}
                  options={vocabulary.directive_status.map((s) => ({ value: s, label: s }))}
                />

                <Field
                  label="Severity"
                  placeholder="All severities"
                  values={filters.severity}
                  onChange={(value) => setOne("severity", value)}
                  options={vocabulary.severity.map((s) => ({ value: s, label: s }))}
                />

                <Field
                  label="Data quality"
                  placeholder="Any"
                  values={filters.quality}
                  onChange={(value) => setOne("quality", value)}
                  options={vocabulary.quality.map((q) => ({ value: q, label: QUALITY_LABEL[q] }))}
                />

                <Field
                  label="Validation flag"
                  placeholder="All directives"
                  values={filters.flag}
                  onChange={(value) => setOne("flag", value)}
                  options={[
                    { value: ANY_FLAG, label: "Flagged — any flag" },
                    ...vocabulary.flag_codes.map((code) => ({
                      value: code,
                      label: code.toLowerCase().replace(/_/g, " "),
                    })),
                  ]}
                />
              </div>

              <div className="panel-row">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={filters.only_open}
                    onChange={(event) =>
                      onFilters({ only_open: event.target.checked, page: 1 })
                    }
                  />
                  Only directives with open work
                </label>

                <label className="field inline">
                  <span className="field-label">Rows</span>
                  <select
                    className="control"
                    value={filters.page_size}
                    onChange={(event) =>
                      onFilters({ page_size: Number(event.target.value), page: 1 })
                    }
                  >
                    {[25, 50, 100, 200].map((size) => (
                      <option key={size} value={size}>
                        {size}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="panel-foot">
                <button className="btn-primary" onClick={() => setOpen(false)}>
                  Done
                </button>
              </div>
            </div>
          )}
        </div>

        {(count > 0 || filters.q) && (
          <button className="btn-link" onClick={onReset}>
            Reset
          </button>
        )}
      </div>

      {activeChips.length > 0 && (
        <div className="active-filters">
          {activeChips.map((chip) => (
            <span className="chip" key={chip.label}>
              {chip.label}
              <button onClick={chip.clear} aria-label={`Remove ${chip.label}`}>
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </>
  );
}
