import type { Flag } from "../types";

/** Compressed label so a row can show three flags without wrapping. */
const SHORT: Record<string, string> = {
  MISSING_FIELD: "MISSING",
  MISSING_DATE: "NO DATE",
  MISSING_DEADLINE: "NO DEADLINE",
  MISSING_STATUS: "NO STATUS",
  MISSING_SEVERITY: "NO SEV",
  UNPARSEABLE_DATE: "BAD DATE",
  DATE_CHRONOLOGY: "CHRONOLOGY",
  CONFLICTING_STATUS: "CONFLICT",
  STATUS_CONFLICT: "CONFLICT",
  UNKNOWN_STATUS: "BAD STATUS",
  UNKNOWN_SEVERITY: "BAD SEV",
  NORMALISED_STATUS: "FIXED STATUS",
  NORMALISED_SEVERITY: "FIXED SEV",
  MALFORMED_TEXT: "MALFORMED",
  TRUNCATED_TEXT: "TRUNCATED",
  OVERLONG_TEXT: "CLIPPED",
  SHOUTING_TEXT: "ALL CAPS",
  DUPLICATE_REFERENCE: "DUPLICATE",
  UNASSIGNED_OWNER: "NO OWNER",
  RESOLVED_WITHOUT_TIMESTAMP: "NO RESOLVED AT",
  TIMESTAMP_WITHOUT_RESOLUTION: "STALE RESOLVED AT",
  DUE_AFTER_DIRECTIVE_DEADLINE: "DUE LATE",
  OVERDUE: "OVERDUE",
};

export function FlagBadge({ flag, count = 1 }: { flag: Flag; count?: number }) {
  const label = SHORT[flag.code] ?? flag.code;
  return (
    <span className={`flag-badge ${flag.level}`} title={`${flag.code} - ${flag.message}`}>
      {count > 1 ? `${label} ×${count}` : label}
    </span>
  );
}

/**
 * One badge per distinct problem. The same code raised on three action items
 * is one fact about the row ("OVERDUE ×3"), not three badges competing for
 * the reader's attention.
 */
export function FlagStrip({ flags, limit = 3 }: { flags: Flag[]; limit?: number }) {
  if (flags.length === 0) return <span className="flag-count">—</span>;

  const grouped = new Map<string, { flag: Flag; count: number }>();
  for (const flag of flags) {
    const entry = grouped.get(flag.code);
    if (entry) {
      entry.count += 1;
      // An error outranks a warning when the same code appears as both.
      if (flag.level === "error") entry.flag = flag;
    } else {
      grouped.set(flag.code, { flag, count: 1 });
    }
  }

  const ordered = [...grouped.values()].sort((a, b) =>
    a.flag.level === b.flag.level ? b.count - a.count : a.flag.level === "error" ? -1 : 1,
  );
  const shown = ordered.slice(0, limit);
  const hidden = ordered.slice(limit);

  return (
    <>
      {shown.map(({ flag, count }) => (
        <FlagBadge key={flag.code} flag={flag} count={count} />
      ))}
      {hidden.length > 0 && (
        <span className="flag-count" title={hidden.map((entry) => entry.flag.code).join(", ")}>
          +{hidden.length}
        </span>
      )}
    </>
  );
}
