export default function CentreList({ centres, selected, onSelect }) {
  return (
    <ul className="centre-grid">
      {centres.map((c, i) => {
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
