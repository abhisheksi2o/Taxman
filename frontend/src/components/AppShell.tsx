"use client";
import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import clsx from "clsx";
import { AlertTriangle, Bot, Calculator, ClipboardCheck, FileText, FlaskConical, History, LayoutDashboard, LogOut, Menu, Moon, Scale, Sun, UserRound, X } from "lucide-react";
import { useApp } from "@/lib/store";
import { Badge, Skeleton } from "./ui";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/profile", label: "My Tax Profile", icon: UserRound },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/reconciliation", label: "Reconciliation", icon: Scale },
  { href: "/computation", label: "Tax Computation", icon: Calculator },
  { href: "/issues", label: "Issues & Alerts", icon: AlertTriangle },
  { href: "/astra", label: "Ask Astra", icon: Bot },
  { href: "/review", label: "Return Review", icon: ClipboardCheck },
];

function useTheme() {
  const [theme, setTheme] = useState<string>("system");
  useEffect(() => {
    try {
      const t = localStorage.getItem("astra.theme") || "system";
      setTheme(t);
      if (t !== "system") document.documentElement.dataset.theme = t;
    } catch {}
  }, []);
  const toggle = () => {
    const isDark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
    const next = isDark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
    try {
      localStorage.setItem("astra.theme", next);
    } catch {}
  };
  return { theme, toggle };
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, loading, cases, activeCaseId, setActiveCase, logout, touch } = useApp();
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const { toggle } = useTheme();
  const isPublic = pathname === "/login";

  useEffect(() => {
    if (!loading && !user && !isPublic) router.replace("/login");
  }, [loading, user, isPublic, router]);

  if (isPublic) return <>{children}</>;
  if (loading || !user) {
    return <div className="mx-auto max-w-5xl space-y-4 p-6"><Skeleton className="h-10 w-1/3" /><Skeleton className="h-40" /><Skeleton className="h-64" /></div>;
  }
  const active = cases.find((c) => c.id === activeCaseId);
  const nav = [...NAV, { href: "/audit", label: "Audit trail", icon: History }, ...(user.dev_mode ? [{ href: "/dev/evaluation", label: "AI Evaluation", icon: FlaskConical }] : [])];

  const sidebar = (
    <aside className="flex h-full w-64 flex-col border-r border-line bg-surface">
      <div className="flex items-center gap-2 px-5 py-5">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand text-brand-fg font-bold">A</div>
        <div>
          <div className="text-sm font-semibold leading-tight">ASTRA Tax</div>
          <div className="text-[11px] text-muted">Filing & reconciliation workspace</div>
        </div>
        <button className="ml-auto rounded-md p-1 text-muted lg:hidden" onClick={() => setOpen(false)} aria-label="Close menu"><X size={18} /></button>
      </div>
      <nav className="scrollbar-thin flex-1 space-y-0.5 overflow-y-auto px-3">
        {nav.map((n) => {
          const current = n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
          const Icon = n.icon;
          return (
            <Link key={n.href} href={n.href} onClick={() => setOpen(false)} className={clsx("flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors", current ? "bg-brand-soft font-medium text-brand" : "text-fg/80 hover:bg-surface-2")}>
              <Icon size={17} /> {n.label}
              {n.href === "/issues" && active && active.open_issues > 0 && <Badge tone="danger" className="ml-auto">{active.open_issues}</Badge>}
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-line p-4 text-xs text-muted">
        <div className="truncate font-medium text-fg">{user.display_name}</div>
        <div className="truncate">{user.is_demo ? "Demo workspace" : user.email}</div>
        <div className="mt-2 flex gap-2">
          <button onClick={toggle} className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 hover:bg-surface-2"><Sun size={12} className="dark:hidden" /><Moon size={12} /> Theme</button>
          <button onClick={async () => { await logout(); router.replace("/login"); }} className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 hover:bg-surface-2"><LogOut size={12} /> Sign out</button>
        </div>
      </div>
    </aside>
  );

  return (
    <div className="flex min-h-dvh">
      <div className="hidden lg:block">{sidebar}</div>
      {open && <div className="fixed inset-0 z-40 flex lg:hidden"><div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} /><div className="relative z-10">{sidebar}</div></div>}
      <div className="flex min-w-0 flex-1 flex-col overflow-x-hidden">
        <header className="sticky top-0 z-30 flex min-w-0 flex-wrap items-center gap-2 border-b border-line bg-surface/90 px-4 py-3 backdrop-blur lg:gap-3 lg:px-8">
          <button className="rounded-md p-1 text-muted lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu"><Menu size={20} /></button>
          {cases.length > 0 ? (
            <select value={activeCaseId || ""} onChange={(e) => { setActiveCase(e.target.value); touch(); }} className="h-9 min-w-0 max-w-[55vw] truncate rounded-lg border border-line bg-surface px-2 text-sm lg:max-w-xs">
              {cases.map((c) => <option key={c.id} value={c.id}>{c.taxpayer_name || c.label} · AY {c.assessment_year}</option>)}
            </select>
          ) : <span className="text-sm text-muted">No return yet</span>}
          {active && <Badge tone="brand">AY {active.assessment_year}</Badge>}
          {active?.demo_scenario && <Badge tone="warning" className="hidden sm:inline-flex" title="Synthetic data – not a real taxpayer">Demo · {active.demo_scenario.replace(/_/g, " ")}</Badge>}
          <div className="ml-auto hidden items-center gap-2 text-xs text-muted md:flex">
            <span className={clsx("h-2 w-2 rounded-full", user.llm_enabled ? "bg-positive" : "bg-warning")} />
            {user.llm_enabled ? "Astra: Claude + deterministic engine" : "Astra: deterministic mode (no LLM key)"}
          </div>
        </header>
        <main className="mx-auto w-full min-w-0 max-w-7xl flex-1 overflow-x-hidden px-4 py-6 lg:px-8">{children}</main>
        <footer className="px-4 pb-6 text-center text-[11px] text-muted lg:px-8">ASTRA Tax prepares estimates under versioned official rules. It does not file returns and is not a substitute for professional advice. Extracted, calculated, confirmed and AI-generated information are always labelled.</footer>
      </div>
    </div>
  );
}
