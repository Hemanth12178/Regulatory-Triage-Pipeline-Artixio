import { Link } from "react-router-dom";
import type { ActionItem, Directive, Flag, Vocabulary } from "../types";
import { FlagBadge } from "./FlagBadge";

/**
 * The assignable statuses and the state machine both come from
 * /api/vocabulary. A second copy here drifts the moment the server's rules
 * change, and the first sign of it is a legal transition greyed out or an
 * illegal one offered.
 */

/** Human labels for the status pills; the code is the source of truth. */
const STATUS_LABEL: Record<string, string> = {
  PENDING: "Pending",
  IN_PROGRESS: "In progress",
  BLOCKED: "Blocked",
  RESOLVED: "Resolved",
  DISMISSED: "Dismissed",
  UNKNOWN: "Unknown",
};

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

/**
 * "30 Oct 2026, 2:56 PM" in the viewer's timezone. The API sends naive UTC
 * (no offset), which `new Date` would otherwise read as local time, so the
 * missing offset is added first. The month is spelled out so the date cannot
 * be misread as day-first or month-first.
 */
function formatUpdated(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(/(Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return null;
  const day = date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
  const time = date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  return `${day}, ${time}`;
}

function initials(name: string): string {
  const parts = name.replace(/[^\p{L}\s.]/gu, "").split(/[\s.]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/**
 * Published -> effective -> deadline, drawn as a track. A date the feed sent
 * but we could not parse shows as a dashed node carrying the raw string,
 * so the gap in the record is visible rather than implied.
 */
function Timeline({ directive }: { directive: Directive }) {
  const stages = [
    { label: "Published", value: directive.published_at, raw: directive.raw_dates.published_at },
    { label: "Comments by", value: directive.comments_by, raw: directive.raw_dates.comments_by },
    { label: "Effective", value: directive.effective_date, raw: directive.raw_dates.effective_date },
    { label: "Deadline", value: directive.deadline, raw: directive.raw_dates.deadline },
  ];
  const days = directive.days_to_deadline;
  const open = directive.open_item_count > 0;

  return (
    <ol className="timeline">
      {stages.map((stage, index) => {
        const isDeadline = index === stages.length - 1;
        const state = stage.value ? "set" : stage.raw ? "unparsed" : "absent";
        return (
          <li key={stage.label} className={`tl-node is-${state}`}>
            <span className="tl-dot" />
            <span className="tl-label">{stage.label}</span>
            <span className="tl-value">
              {stage.value ?? (
                <span className="missing">{stage.raw ? `“${stage.raw}”` : "not supplied"}</span>
              )}
            </span>
            {isDeadline && stage.value && days !== null && (
              <span className={`tl-note ${days < 0 && open ? "is-late" : ""}`}>
                {days < 0 ? `${-days}d overdue` : `in ${days}d`}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function FlagList({ flags }: { flags: Flag[] }) {
  if (flags.length === 0) {
    return (
      <p className="all-clear">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="m5 12.5 4.5 4.5L19 7.5" />
        </svg>
        Every field passed validation.
      </p>
    );
  }
  return (
    <ul className="flag-list">
      {flags.map((flag, index) => (
        <li key={`${flag.code}-${flag.field}-${index}`}>
          <FlagBadge flag={flag} />
          <span>{flag.message}</span>
        </li>
      ))}
    </ul>
  );
}

function Task({
  item,
  pending,
  vocabulary,
  onChangeStatus,
}: {
  item: ActionItem;
  pending: boolean;
  vocabulary: Vocabulary;
  onChangeStatus: (item: ActionItem, next: string) => void;
}) {
  const assignable = vocabulary.assignable_action_status;
  // No entry means the server has not told us yet; offer everything and let
  // it reject rather than blocking the user on a cold cache.
  const allowed = new Set(vocabulary.action_transitions[item.status] ?? assignable);
  const overdue = item.flags.some((f) => f.code === "OVERDUE");
  const updatedOn = formatUpdated(item.updated_at);

  return (
    <li className={`task ${item.is_open ? "" : "is-done"}`}>
      <div className="task-body">
        <span className="task-title">
          <span className="task-id">#{item.id}</span>
          {item.title ?? <span className="missing">title missing</span>}
        </span>

        {/* Status as a pill group instead of a dropdown: the whole state
            machine is visible at once, and transitions the server would
            reject are shown disabled rather than hidden. */}
        <div
          className="status-pills"
          role="group"
          aria-label={`Status for action item ${item.id}`}
        >
          {!assignable.includes(item.status) && (
            <span
              className="status-pill is-current is-unknown"
              title="Unmapped status — needs triage"
            >
              {statusLabel(item.status)} ⚠
            </span>
          )}
          {assignable.map((status) => {
            const isCurrent = status === item.status;
            const canSet = isCurrent || allowed.has(status);
            return (
              <button
                key={status}
                type="button"
                className={`status-pill ${isCurrent ? "is-current" : ""}`}
                disabled={pending || !canSet}
                aria-pressed={isCurrent}
                title={
                  !canSet ? `Cannot move from ${item.status} to ${status}` : undefined
                }
                onClick={() => onChangeStatus(item, status)}
              >
                {statusLabel(status)}
              </button>
            );
          })}
        </div>

        <span className="task-meta">
          <span className="owner">
            <span className="avatar" aria-hidden="true">
              {item.owner ? initials(item.owner) : "—"}
            </span>
            {item.owner ?? <span className="missing">unassigned</span>}
          </span>

          <span className={overdue ? "overdue" : undefined}>
            due{" "}
            {item.due_date ?? (
              <span className="missing">{item.raw_due_date ? `“${item.raw_due_date}”` : "—"}</span>
            )}
          </span>

          <span className={`prio sev-${item.priority}`}>{item.priority}</span>
        </span>

        {updatedOn && <span className="task-updated">Updated on: {updatedOn}</span>}

        {item.notes && <p className="task-notes">{item.notes}</p>}
      </div>

      <div className="task-flags">
        {item.flags.map((flag, index) => (
          <FlagBadge key={`${flag.code}-${index}`} flag={flag} />
        ))}
      </div>
    </li>
  );
}

export function DirectiveDetail({
  directive,
  pendingItemIds,
  vocabulary,
  onChangeStatus,
  onClose,
}: {
  directive: Directive;
  pendingItemIds: Set<number>;
  vocabulary: Vocabulary;
  onChangeStatus: (item: ActionItem, next: string) => void;
  onClose?: () => void;
}) {
  return (
    <div className="drawer">
      <div className="drawer-head">
        <div className="drawer-id">
          <span className="drawer-ref">
            {directive.reference_code ?? <span className="missing">no reference code</span>}
          </span>
          <h3 className="drawer-title">
            {directive.title ?? (
              <span className="missing">Title could not be extracted from the source</span>
            )}
          </h3>
        </div>
        <div className="drawer-actions">
          {directive.source_url && (
            <Link
              className="drawer-source"
              to={`/sample?ref=${encodeURIComponent(directive.reference_code ?? "")}`}
              target="_blank"
            >
              View source
            </Link>
          )}
          {onClose && (
            <button
              type="button"
              className="drawer-close"
              onClick={onClose}
              aria-label="Close detail panel"
              title="Close"
            >
              ×
            </button>
          )}
        </div>
      </div>

      <dl className="meta-grid">
        <div className="meta-cell">
          <dt>Authority</dt>
          <dd>
            <span className="meta-chip strong">{directive.authority.code}</span>
            <span className="meta-full" title={directive.authority.name}>
              {directive.authority.name}
            </span>
          </dd>
        </div>

        <div className="meta-cell">
          <dt>Category</dt>
          <dd>
            {directive.category ?? <span className="missing">not categorised</span>}
          </dd>
        </div>

        <div className="meta-cell">
          <dt>Severity</dt>
          <dd>
            <span className={`tag sev-${directive.severity}`}>{directive.severity}</span>
            {directive.raw_severity &&
              directive.raw_severity.trim().toUpperCase() !== directive.severity && (
                <span className="raw-value" title="What the feed actually sent">
                  feed: {directive.raw_severity}
                </span>
              )}
          </dd>
        </div>

        <div className="meta-cell">
          <dt>Status</dt>
          <dd>
            <span className={`st st-${directive.status}`}>{directive.status}</span>
            {directive.raw_status && directive.raw_status !== directive.status && (
              <span className="raw-value" title="What the feed actually sent">
                feed: {directive.raw_status}
              </span>
            )}
          </dd>
        </div>
      </dl>

      <p className="drawer-summary">
        {directive.summary ?? (
          <span className="missing">No summary was extracted from the source document.</span>
        )}
      </p>

      <Timeline directive={directive} />

      <div className="drawer-cols">
        <section className="drawer-block">
          <h4>
            Validation
            <span className="count-badge">{directive.flags.length}</span>
          </h4>
          <FlagList flags={directive.flags} />
        </section>

        <section className="drawer-block">
          <h4>
            Action items
            <span className="count-badge">
              {directive.open_item_count} open / {directive.total_item_count}
            </span>
          </h4>

          {directive.action_items.length === 0 ? (
            <p className="all-clear muted">
              Nothing is tracking this directive — no action item has been raised.
            </p>
          ) : (
            <ul className="task-list">
              {directive.action_items.map((item) => (
                <Task
                  key={item.id}
                  item={item}
                  pending={pendingItemIds.has(item.id)}
                  vocabulary={vocabulary}
                  onChangeStatus={onChangeStatus}
                />
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
