import type { Filters, Quality, Stats } from "../types";

/**
 * One 34px line, not a wall of cards. Each counter is also a filter: the
 * fastest path from "21 records are flagged" to "show me those 21".
 */
export function StatsStrip({
  stats,
  filters,
  onFilters,
}: {
  stats: Stats;
  filters: Filters;
  onFilters: (patch: Partial<Filters>) => void;
}) {
  const toggleQuality = (quality: Quality) =>
    onFilters({
      quality: filters.quality.includes(quality)
        ? filters.quality.filter((q) => q !== quality)
        : [...filters.quality, quality],
      page: 1,
    });

  const toggleFlag = (code: string) =>
    onFilters({
      flag: filters.flag.includes(code)
        ? filters.flag.filter((f) => f !== code)
        : [...filters.flag, code],
      page: 1,
    });

  const topFlags = Object.entries(stats.by_flag).slice(0, 5);

  return (
    <div className="stats">
      <span className="stat" style={{ cursor: "default" }}>
        <span className="n">{stats.directives}</span>
        <span className="k">directives</span>
      </span>
      <span className="stat" style={{ cursor: "default" }}>
        <span className="n">{stats.open_action_items}</span>
        <span className="k">open of {stats.action_items} items</span>
      </span>

      <i className="stat-divider" />

      <button
        className={`stat is-error ${filters.quality.includes("error") ? "active" : ""}`}
        onClick={() => toggleQuality("error")}
        title="Rows with at least one error-level flag"
      >
        <span className="n">{stats.by_quality.error}</span>
        <span className="k">error</span>
      </button>
      <button
        className={`stat is-warn ${filters.quality.includes("warning") ? "active" : ""}`}
        onClick={() => toggleQuality("warning")}
        title="Rows repaired on read, with warnings"
      >
        <span className="n">{stats.by_quality.warning}</span>
        <span className="k">warning</span>
      </button>
      <button
        className={`stat is-ok ${filters.quality.includes("ok") ? "active" : ""}`}
        onClick={() => toggleQuality("ok")}
        title="Rows that passed every validation"
      >
        <span className="n">{stats.by_quality.ok}</span>
        <span className="k">clean</span>
      </button>

      {topFlags.length > 0 && <i className="stat-divider" />}

      {topFlags.map(([code, count]) => (
        <button
          key={code}
          className={`stat ${filters.flag.includes(code) ? "active" : ""}`}
          onClick={() => toggleFlag(code)}
          title={`Filter to rows carrying ${code}`}
        >
          <span className="n">{count}</span>
          <span className="k">{code.toLowerCase().replace(/_/g, " ")}</span>
        </button>
      ))}
    </div>
  );
}
