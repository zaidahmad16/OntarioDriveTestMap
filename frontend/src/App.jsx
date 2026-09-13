import { useEffect, useState } from "react";
import { api } from "./api.js";
import Login from "./components/Login.jsx";
import CentreList from "./components/CentreList.jsx";
import MapView from "./components/MapView.jsx";
import TraceList from "./components/TraceList.jsx";

export default function App() {
  const [user, setUser] = useState(null);
  const [checkedAuth, setCheckedAuth] = useState(false);
  const [centres, setCentres] = useState([]);
  const [selectedCentre, setSelectedCentre] = useState(null);

  // Centre list is public, so load it regardless of sign-in state -- it's
  // what a visitor sees before deciding whether to sign in at all.
  useEffect(() => {
    api.getCentres().then(setCentres);
  }, []);

  useEffect(() => {
    api
      .me()
      .then((u) => setUser(u))
      .catch(() => setUser(null))
      .finally(() => setCheckedAuth(true));
  }, []);

  if (!checkedAuth) return <p>Loading…</p>;

  return (
    <div style={{ maxWidth: 1000, margin: "0 auto", padding: 20 }}>
      <h1>OntarioDriveTestMap</h1>

      {!user ? (
        <div>
          <p>Sign in with Google to view route data.</p>
          <Login onLogin={setUser} />
        </div>
      ) : (
        <div style={{ marginBottom: 16 }}>
          Signed in as {user.email}{" "}
          <button
            onClick={() => api.logout().then(() => setUser(null))}
          >
            sign out
          </button>
        </div>
      )}

      <h2>Centres</h2>
      <CentreList
        centres={centres}
        selected={selectedCentre}
        onSelect={setSelectedCentre}
      />

      {selectedCentre && !user && (
        <p>Sign in to view this centre's map and sources.</p>
      )}

      {selectedCentre && user && (
        <>
          <MapView centreId={selectedCentre} />
          <TraceList centreId={selectedCentre} />
        </>
      )}
    </div>
  );
}