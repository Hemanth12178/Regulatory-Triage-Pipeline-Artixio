import { Link, useSearchParams } from "react-router-dom";

/** Placeholder landing page for a directive's "View source" link. */
export default function SamplePage() {
  const [params] = useSearchParams();
  const reference = params.get("ref");

  return (
    <main className="sample-page">
      {reference && <p className="sample-ref">{reference}</p>}
      <h1>Sample source page</h1>
      <p>This is a placeholder for the original source document.</p>
      <Link to="/triage" className="btn-soft">
        Back to triage
      </Link>
    </main>
  );
}
