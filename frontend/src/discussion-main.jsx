import React from "react";
import ReactDOM from "react-dom/client";
import DiscussionPage from "./DiscussionPage.jsx";
import { LangProvider } from "./i18n.jsx";
import "./App.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <LangProvider>
      <DiscussionPage />
    </LangProvider>
  </React.StrictMode>
);
