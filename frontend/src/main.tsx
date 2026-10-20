import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { Layout } from "./components/Layout";
import { JobPage } from "./pages/JobPage";
import { JobsPage } from "./pages/JobsPage";
import { NewJobPage } from "./pages/NewJobPage";
import "./styles/global.css";

try {
  const theme = localStorage.getItem("arm.theme");
  if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
} catch {
  /* тема по умолчанию — как в системе */
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <JobsPage /> },
      { path: "/new", element: <NewJobPage /> },
      { path: "/jobs/:jobId/:tab?", element: <JobPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
