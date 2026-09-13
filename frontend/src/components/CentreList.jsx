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
            {c.route_line_count} route lines
          </button>
        </li>
      ))}
    </ul>
  );
}