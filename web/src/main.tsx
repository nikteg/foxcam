import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";

import { Layout, NotFound } from "./App";
import { Account } from "./pages/Account";
import { InvitePage, LoginPage } from "./pages/Login";
import { I18nProvider } from "./i18n";
import { Cameras } from "./pages/Cameras";
import { Dashboard } from "./pages/Dashboard";
import { Detections } from "./pages/Detections";
import { Events } from "./pages/Events";
import { Live } from "./pages/Live";
import { MaskEditor } from "./pages/MaskEditor";
import { Queue } from "./pages/Queue";
import { Recordings } from "./pages/Recordings";
import { Settings } from "./pages/Settings";
import { ThemeProvider } from "./theme";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: true } },
});

const router = createBrowserRouter([
  { path: "/login", Component: LoginPage },
  { path: "/invite/:token", Component: InvitePage },
  {
    path: "/",
    Component: Layout,
    children: [
      { index: true, Component: Dashboard },
      { path: "live", Component: Live },
      { path: "events", Component: Events },
      { path: "detections", Component: Detections },
      { path: "recordings", Component: Recordings },
      { path: "recordings/joined", Component: Recordings },
      { path: "jobs", Component: Queue },
      { path: "jobs/workers", Component: Queue },
      { path: "settings", Component: Settings },
      { path: "settings/:subPage", Component: Settings },
      { path: "account", Component: Account },
      { path: "cameras", Component: Cameras },
      { path: "cameras/profiles", Component: Cameras },
      { path: "cameras/integrations", Component: Cameras },
      { path: "cameras/:cameraId/mask", Component: MaskEditor },
      { path: "*", Component: NotFound },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      {/* Not a FluentProvider className: Fluent copies those onto its tooltip/popover portal
          containers, which would then get the full-page background. */}
      <div className="app-root">
        <QueryClientProvider client={queryClient}>
          <I18nProvider>
            <RouterProvider router={router} />
          </I18nProvider>
        </QueryClientProvider>
      </div>
    </ThemeProvider>
  </StrictMode>,
);
