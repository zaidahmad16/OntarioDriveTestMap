import { useRef, useState } from "react";
import { api } from "./api.js";

// Shared @mention rendering + autocomplete, used by both the forum
// ("where I messed up") and the general discussion board -- extracted
// here because both need the exact same behavior, not because it might
// be useful someday.

// Renders @username tokens highlighted, like a real mention -- purely
// display-side, doesn't validate the mention resolves to a real user.
export function MentionText({ text }) {
  if (!text) return null;
  const parts = text.split(/(@\w+)/g);
  return (
    <>
      {parts.map((part, i) =>
        /^@\w+$/.test(part) ? (
          <span key={i} style={{ color: "var(--accent)", fontWeight: "bold" }}>
            {part}
          </span>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  );
}

// A plain textarea/input that shows a live @username autocomplete
// dropdown while the text right before the cursor looks like "@partial".
export function MentionField({ value, onChange, onKeyDown, placeholder, rows, style }) {
  const [suggestions, setSuggestions] = useState([]);
  const ref = useRef(null);

  function handleChange(e) {
    const val = e.target.value;
    onChange(val);
    const pos = e.target.selectionStart;
    const match = val.slice(0, pos).match(/@(\w+)$/);
    if (match) {
      api
        .searchUsers(match[1])
        .then((users) => setSuggestions(users.map((u) => u.username)))
        .catch(() => setSuggestions([]));
    } else if (suggestions.length) {
      setSuggestions([]);
    }
  }

  function pick(username) {
    const el = ref.current;
    const pos = el.selectionStart;
    const before = value.slice(0, pos).replace(/@(\w+)$/, `@${username} `);
    onChange(before + value.slice(pos));
    setSuggestions([]);
    el.focus();
  }

  const Tag = rows ? "textarea" : "input";
  return (
    <div style={{ position: "relative" }}>
      <Tag
        ref={ref}
        value={value}
        onChange={handleChange}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        rows={rows}
        style={style}
      />
      {suggestions.length > 0 && (
        <div
          className="popover"
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            marginTop: 4,
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-sm)",
            zIndex: "var(--z-dropdown)",
            minWidth: 140,
            overflow: "hidden",
          }}
        >
          {suggestions.map((u) => (
            <div
              key={u}
              onClick={() => pick(u)}
              style={{ padding: "6px 10px", cursor: "pointer", fontSize: "0.85em" }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface-sunken)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              @{u}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
