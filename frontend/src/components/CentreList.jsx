export default function CentreList({ centres, selected, onSelect }) {
  return (
    <ul style={{ listStyle: "none", padding: 0 }}>
      {centres.map((c) => (
        <li key={c.id} style={{ marginBottom: 8 }}>
          <button
            onClick={() => onSelect(c.id)}
            style={{
              fontWeight: selected === c.id ? "bold" : "normal",
              padding: "6px 10px",
              cursor: "pointer",
            }}
          >
            {c.name} — {c.trace_count} traces, {c.segment_count} segments,{" "}
            {/* Show confirmed routes as the headline number; inferred ones
                (predicted bridges, below-threshold recovery, centre
                connectors) are real work but must never be presented as
                indistinguishable from confirmed consensus routes. */}
            {c.confirmed_route_line_count ?? c.route_line_count} confirmed route
            {(c.confirmed_route_line_count ?? c.route_line_count) === 1 ? "" : "s"}
            {c.route_line_count > (c.confirmed_route_line_count ?? c.route_line_count)
              ? ` (+${c.route_line_count - (c.confirmed_route_line_count ?? c.route_line_count)} inferred)`
              : ""}
          </button>
        </li>
      ))}
    </ul>
  );
}