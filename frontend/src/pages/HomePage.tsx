import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchAuthorities, fetchDirectives } from "../lib/api";
import { DEFAULT_FILTERS } from "../types";
import { BrandMark } from "../components/BrandMark";

/** The whole corpus, so the figures below describe the real database. */
const ALL = { ...DEFAULT_FILTERS, page_size: 200 };

const CAPABILITIES = [
  {
    title: "Ingest without loss",
    body: "Feeds arrive malformed — quarter labels where dates belong, markup in titles, statuses that contradict themselves. Every field is stored exactly as received rather than rejected at the door.",
    icon: (
      <>
        <path d="M12 3v11" />
        <path d="m8 10 4 4 4-4" />
        <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
      </>
    ),
  },
  {
    title: "Validate on read",
    body: "Canonical values are derived at the API boundary. Anything that cannot be trusted becomes null and raises a flag explaining why — nothing is silently guessed, nothing is silently dropped.",
    icon: (
      <>
        <path d="M12 3 4.5 6v5.2c0 4.3 3 8 7.5 9.3 4.5-1.3 7.5-5 7.5-9.3V6L12 3Z" />
        <path d="m9 12 2.2 2.2L15.5 10" />
      </>
    ),
  },
  {
    title: "Triage with confidence",
    body: "Action items move through a validated state machine. Terminal states cannot be edited sideways, dismissals need a justification, and concurrent edits are refused rather than overwritten.",
    icon: (
      <>
        <path d="M4 7h10" />
        <path d="M4 12h16" />
        <path d="M4 17h7" />
        <circle cx="17.5" cy="7" r="2.2" />
        <circle cx="14.5" cy="17" r="2.2" />
      </>
    ),
  },
];

const PIPELINE = [
  { step: "01", title: "Authority", note: "FDA, EMA, PMDA and five more" },
  { step: "02", title: "Directive", note: "raw feed text, kept verbatim" },
  { step: "03", title: "Action item", note: "what a human actually resolves" },
];

function Figure({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="figure">
      <span className="figure-value">{value}</span>
      <span className="figure-label">{label}</span>
    </div>
  );
}

export default function HomePage() {
  const directives = useQuery({
    queryKey: ["directives", ALL],
    queryFn: () => fetchDirectives(ALL),
    staleTime: 60 * 1000,
  });
  const authorities = useQuery({
    queryKey: ["authorities"],
    queryFn: fetchAuthorities,
    staleTime: 10 * 60 * 1000,
  });

  const stats = directives.data?.stats;
  // Dashes rather than zeros while loading: a zero here would read as a fact.
  const show = (n: number | undefined) => (stats && n !== undefined ? n : "—");
  const flagged = stats ? stats.by_quality.error + stats.by_quality.warning : undefined;

  return (
    <div className="home">
      <header className="home-nav">
        <div className="brand">
          <BrandMark className="brand-mark lg" />
          <span className="brand-text">
            <span className="brand-name lg">ARTIXIO</span>
            <span className="brand-product">Regulatory Intelligence</span>
          </span>
        </div>
        <span className="spacer" />
        <a className="btn-ghost" href="http://localhost:8000/docs" target="_blank" rel="noreferrer">
          API docs
        </a>
        <Link to="/triage" className="btn-primary">
          Open console
        </Link>
      </header>

      <main className="home-main">
        <section className="hero">
          <h2>
            Turn a messy regulatory feed into
            <br />
            work someone can actually close.
          </h2>
          <p className="lead">
            Directives arrive from eight authorities in whatever shape the source published them.
            This console parses what it can, flags what it cannot, and never hides either from the
            compliance officer reading it.
          </p>
          <div className="hero-actions">
            <Link to="/triage" className="btn-primary btn-lg">
              Open the triage console →
            </Link>
            <span className="hero-hint">
              {directives.isError
                ? "Backend offline — start uvicorn on port 8000"
                : `${show(stats?.directives)} directives ready to review`}
            </span>
          </div>
        </section>

        <section className="figures" aria-label="Current database">
          <Figure value={show(stats?.directives)} label="Directives tracked" />
          <Figure value={authorities.data ? authorities.data.length : "—"} label="Authorities" />
          <Figure value={show(stats?.open_action_items)} label="Open action items" />
          <Figure value={show(flagged)} label="Records needing review" />
        </section>

        <section className="cards">
          {CAPABILITIES.map((capability) => (
            <article className="card" key={capability.title}>
              <svg className="card-icon" viewBox="0 0 24 24" aria-hidden="true">
                {capability.icon}
              </svg>
              <h3>{capability.title}</h3>
              <p>{capability.body}</p>
            </article>
          ))}
        </section>

        <section className="pipeline">
          <div className="pipeline-head">
            <h3>Three entities, two relationships</h3>
            <p>
              An authority issues many directives; a directive generates many action items. Raw feed
              values live alongside the canonical ones, so the audit trail is never lost.
            </p>
          </div>
          <ol className="pipeline-steps">
            {PIPELINE.map((stage) => (
              <li key={stage.step}>
                <span className="pipeline-step">{stage.step}</span>
                <span className="pipeline-title">{stage.title}</span>
                <span className="pipeline-note">{stage.note}</span>
              </li>
            ))}
          </ol>
        </section>
      </main>

      <footer className="home-footer">
        <BrandMark className="brand-mark sm" />
        <span>
          <strong className="brand-name">ARTIXIO</strong> · Regulatory Intelligence Triage Pipeline
        </span>
        <span className="spacer" />
        <Link to="/triage">Open console</Link>
      </footer>
    </div>
  );
}
