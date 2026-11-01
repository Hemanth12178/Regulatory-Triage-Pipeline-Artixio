/** Shield = compliance, pulse line = the live monitoring feed. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
      <path
        className="brand-shield"
        d="M12 2.4 4.2 5.6v5.6c0 4.6 3.2 8.4 7.8 9.8 4.6-1.4 7.8-5.2 7.8-9.8V5.6L12 2.4Z"
      />
      <path className="brand-pulse" d="M7.4 12.1h2.3l1.3-3.1 2 5.7 1.2-2.6h2.4" />
    </svg>
  );
}
