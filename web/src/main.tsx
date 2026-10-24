import { FluentProvider } from "@fluentui/react-components";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";

import { Layout, NotFound } from "./App";
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
import { theme } from "./theme";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: true } },
});

const router = createBrowserRouter([
  {
    path: "/",
    Component: Layout,
    children: [
      { index: true, Component: Dashboard },
      { path: "live", Component: Live },
      { path: "events", Component: Events },
      { path: "detections", Component: Detections },
      { path: "recordings", Component: Recordings },
      { path: "queue", Component: Queue },
      { path: "settings", Component: Settings },
      { path: "cameras", Component: Cameras },
      { path: "cameras/:cameraId/mask", Component: MaskEditor },
      { path: "*", Component: NotFound },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <FluentProvider theme={theme}>
      {/* Not a FluentProvider className: Fluent copies those onto its tooltip/popover portal
          containers, which would then get the full-page background. */}
      <div className="app-root">
        <QueryClientProvider client={queryClient}>
          <I18nProvider>
            <RouterProvider router={router} />
          </I18nProvider>
        </QueryClientProvider>
      </div>
    </FluentProvider>
  </StrictMode>,
);
