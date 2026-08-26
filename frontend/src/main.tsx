import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import { AuthProvider, useAuth } from "./lib/auth";
import Login from "./pages/Login";
import Landing from "./pages/Landing";
import Layout from "./pages/Layout";
import ControlCenter from "./pages/ControlCenter";
import Cases from "./pages/Cases";
import CaseDetail from "./pages/CaseDetail";
import Experiments from "./pages/Experiments";
import Policies from "./pages/Policies";
import Simulator from "./pages/Simulator";

function Guard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-zinc-500">
        Loading…
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <HashRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Landing />} />
          <Route
            path="/console"
            element={
              <Guard>
                <Layout />
              </Guard>
            }
          >
            <Route index element={<ControlCenter />} />
            <Route path="cases" element={<Cases />} />
            <Route path="cases/:id" element={<CaseDetail />} />
            <Route path="experiments" element={<Experiments />} />
            <Route path="policies" element={<Policies />} />
            <Route path="simulator" element={<Simulator />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </HashRouter>
    </AuthProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
