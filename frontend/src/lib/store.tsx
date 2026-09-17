"use client";
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Api, ApiError } from "./api";

export type User = { id: string; email: string; display_name: string; role: string; is_demo: boolean; dev_mode: boolean; llm_enabled: boolean };
export type CaseSummary = { id: string; label: string; assessment_year: string; taxpayer_name: string | null; demo_scenario: string | null; onboarding_completed: boolean; open_issues: number; documents: number; updated_at: string };

type AppState = {
  user: User | null;
  loading: boolean;
  cases: CaseSummary[];
  activeCaseId: string | null;
  activeCase: CaseSummary | null;
  supportedYears: string[];
  currentYear: string;
  refreshUser: () => Promise<void>;
  refreshCases: () => Promise<CaseSummary[]>;
  setActiveCase: (id: string | null) => void;
  logout: () => Promise<void>;
  bump: number;
  touch: () => void;
};

const Ctx = createContext<AppState | null>(null);
const KEY = "astra.activeCase";

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [activeCaseId, setActive] = useState<string | null>(null);
  const [supportedYears, setYears] = useState<string[]>([]);
  const [currentYear, setCurrentYear] = useState("2026-27");
  const [bump, setBump] = useState(0);

  const refreshCases = useCallback(async () => {
    try {
      const res = await Api.cases();
      setCases(res.cases);
      setYears(res.supported_years);
      setCurrentYear(res.current_year);
      let stored: string | null = null;
      try {
        stored = localStorage.getItem(KEY);
      } catch {}
      const ids = res.cases.map((c: CaseSummary) => c.id);
      const next = stored && ids.includes(stored) ? stored : ids[0] || null;
      setActive(next);
      return res.cases;
    } catch {
      setCases([]);
      return [];
    }
  }, []);

  const refreshUser = useCallback(async () => {
    setLoading(true);
    try {
      const me = await Api.me();
      setUser(me);
      await refreshCases();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setUser(null);
    } finally {
      setLoading(false);
    }
  }, [refreshCases]);

  useEffect(() => {
    refreshUser();
  }, [refreshUser]);

  const setActiveCase = useCallback((id: string | null) => {
    setActive(id);
    try {
      if (id) localStorage.setItem(KEY, id);
      else localStorage.removeItem(KEY);
    } catch {}
  }, []);

  const logout = useCallback(async () => {
    try {
      await Api.logout();
    } finally {
      setUser(null);
      setCases([]);
      setActive(null);
    }
  }, []);

  const value = useMemo<AppState>(
    () => ({
      user, loading, cases, activeCaseId, activeCase: cases.find((c) => c.id === activeCaseId) || null, supportedYears, currentYear,
      refreshUser, refreshCases, setActiveCase, logout, bump, touch: () => setBump((b) => b + 1),
    }),
    [user, loading, cases, activeCaseId, supportedYears, currentYear, refreshUser, refreshCases, setActiveCase, logout, bump],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside provider");
  return v;
}

/** Fetch helper for case-scoped pages: re-runs when the active case or the global bump changes. */
export function useCaseData<T>(loader: (caseId: string) => Promise<T>, deps: unknown[] = []) {
  const { activeCaseId, bump } = useApp();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    if (!activeCaseId) {
      setData(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    loader(activeCaseId)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeCaseId, bump, tick, ...deps]);
  return { data, error, loading, reload: () => setTick((t) => t + 1), setData };
}
