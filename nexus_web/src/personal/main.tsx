import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { App } from "../app/App";
import { AuthProvider } from "../app/AuthContext";
import { personalDistribution } from "./application";
import { personalContextDirectory } from "./contextDirectory";
import "../styles.css";

// A personal installation has one server-owned context. Never adopt a stale
// organization selection or legacy bearer from another host on this origin.
localStorage.removeItem("nexus.console.context");
localStorage.removeItem("nexus.console.auth");
const queryClient = new QueryClient({ defaultOptions: { queries: {
  staleTime: 20_000, retry: 1, refetchOnWindowFocus: false,
} } });
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider contextDirectory={personalContextDirectory}>
        <App distribution={personalDistribution} />
        <Toaster richColors position="top-right" />
      </AuthProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
