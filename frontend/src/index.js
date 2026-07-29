import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/index.css";
import App from "@/App";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    },
  },
});

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);

// PWA: register service worker for offline support + installability.
// We additionally enforce an update lifecycle so users never get stuck on
// a stale bundle after a redeploy:
//   1. On register, listen for `updatefound` and watch the new worker's
//      state. Once it reaches `installed` while an old controller is
//      still active, tell it to `SKIP_WAITING` so it activates now.
//   2. Listen for `controllerchange` at the navigator level and reload
//      the page once — the new SW is now controlling the tab.
//   3. Proactively call `reg.update()` on load so returning PWA users
//      pick up a new build on their next visit without hard-refreshing.
if ('serviceWorker' in navigator) {
  let hasReloadedForNewSW = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (hasReloadedForNewSW) return;
    hasReloadedForNewSW = true;
    window.location.reload();
  });

  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js')
      .then((reg) => {
        // Proactively check for a new SW every time the app boots.
        try { reg.update(); } catch (_) { /* noop */ }

        reg.addEventListener('updatefound', () => {
          const installing = reg.installing;
          if (!installing) return;
          installing.addEventListener('statechange', () => {
            if (
              installing.state === 'installed' &&
              navigator.serviceWorker.controller
            ) {
              // A new worker is waiting while the old one still controls
              // the page — kick it live immediately.
              try { installing.postMessage('SKIP_WAITING'); } catch (_) { /* noop */ }
            }
          });
        });
      })
      .catch(() => {
        /* silent — SW is a progressive enhancement */
      });
  });
}
