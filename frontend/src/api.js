const API_URL = import.meta.env.VITE_API_URL;

async function request(path, options = {}) {
  const res = await fetch(`${API_URL}${path}`, {
    credentials: "include", // required to send/receive the httpOnly session cookie
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export const api = {
  loginWithGoogle: (credential) =>
    request("/auth/google", {
      method: "POST",
      body: JSON.stringify({ credential }),
    }),
  logout: () => request("/auth/logout", { method: "POST" }),
  me: () => request("/auth/me"),
  getCentres: () => request("/centres"),
  getCentre: (id) => request(`/centres/${id}`),
  getMap: (id) => request(`/centres/${id}/map`),
  getTraces: (id) => request(`/centres/${id}/traces`),
  getTrace: (id) => request(`/traces/${id}`),
};