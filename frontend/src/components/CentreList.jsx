export default function CentreList({ centres, selected, onSelect }) {
  // Hide centres with genuinely nothing to show yet (0 traces, 0
  // segments -- e.g. Winchester as of 2026-09) from the browsing grid.
  // Not removed from the underlying `centres` data itself: it still
  // needs to be a valid choice in the submission wizard/forum/discussion
  // centre dropdowns, since someone contributing the first real data for
  // it is exactly how it stops being empty.
  const visible = centres.filter((c) => c.trace_count > 0 || c.segment_count > 0);
  return (
    <ul className="centre-grid">
      {visible.map((c, i) => {
        const confirmed = c.confirmed_route_line_count ?? c.route_line_count;
        const inferred = c.route_line_count - confirmed;
        return (
          <li key={c.id}>
            <a
              href={`/?centre=${encodeURIComponent(c.id)}`}
              onClick={(e) => {
                e.preventDefault();
                onSelect(c.id);
              }}
              className={`centre-card card-interactive rise-in${
                selected === c.id ? " centre-card--selected" : ""
              }`}
              style={{ animationDelay: `${i * 40}ms` }}
            >
              <span className="centre-card__name">{c.name}</span>
              <span className="centre-card__meta">
                <span className="data">{c.trace_count}</span> traces ·{" "}
                <span className="data">{c.segment_count}</span> segments
              </span>
              <span className="centre-card__meta">
                {/* Confirmed routes are the headline number; inferred ones
                    (predicted bridges, below-threshold recovery, centre
                    connectors) are real work but must never be presented
                    as indistinguishable from confirmed consensus routes. */}
                <span className="data" style={{ color: "var(--confirmed)" }}>
                  {confirmed}
                </span>{" "}
                confirmed route{confirmed === 1 ? "" : "s"}
                {inferred > 0 && (
                  <span className="centre-card__inferred"> (+{inferred} inferred)</span>
                )}
              </span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}
