import { createContext, useContext, useEffect, useState } from "react";
import { api, clearToken, getToken } from "./api";

export interface CurrentUser {
  id: string;
  email: string;
  full_name: string;
  role: string;
  merchant_id: string | null;
  auth_mode: string;
}

interface AuthState {
  user: CurrentUser | null;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthState>({
  user: null,
  loading: true,
  refresh: async () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await api<CurrentUser>("/auth/me"));
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    const onUnauthorized = () => setUser(null);
    window.addEventListener("revora:unauthorized", onUnauthorized);
    return () => window.removeEventListener("revora:unauthorized", onUnauthorized);
  }, []);

  return (
    <Ctx.Provider
      value={{
        user,
        loading,
        refresh,
        logout: () => {
          clearToken();
          setUser(null);
        },
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useAuth() {
  return useContext(Ctx);
}
