"use client";
import React from "react";
import clsx from "clsx";
import { X } from "lucide-react";
import { inr, SEVERITY_META, STATUS_META } from "@/lib/format";

export function Card({ title, subtitle, action, children, className, padded = true }: { title?: React.ReactNode; subtitle?: React.ReactNode; action?: React.ReactNode; children?: React.ReactNode; className?: string; padded?: boolean }) {
  return (
    <section className={clsx("card min-w-0", padded && "p-5", className)}>
      {(title || action) && (
        <header className={clsx("flex items-start justify-between gap-3", padded ? "mb-4" : "p-5 pb-3")}>
          <div>
            {title && <h2 className="text-sm font-semibold tracking-tight text-fg">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

const TONES: Record<string, string> = {
  neutral: "bg-surface-2 text-muted border-line",
  brand: "bg-brand-soft text-brand border-transparent",
  positive: "bg-positive-soft text-positive border-transparent",
  warning: "bg-warning-soft text-warning border-transparent",
  danger: "bg-danger-soft text-danger border-transparent",
  info: "bg-info-soft text-info border-transparent",
};

export function Badge({ tone = "neutral", children, className, title }: { tone?: string; children: React.ReactNode; className?: string; title?: string }) {
  return <span title={title} className={clsx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 whitespace-nowrap", TONES[tone] || TONES.neutral, className)}>{children}</span>;
}

export function StatusBadge({ status }: { status?: string | null }) {
  if (!status) return null;
  const m = STATUS_META[status] || { label: status, tone: "neutral" };
  return <Badge tone={m.tone} title="How this value entered the return">{m.label}</Badge>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  const m = SEVERITY_META[severity] || { label: severity, tone: "neutral", icon: "•" };
  return <Badge tone={m.tone}><span aria-hidden>{m.icon}</span>{m.label}</Badge>;
}

export function Button({ variant = "primary", size = "md", loading, className, children, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" | "md"; loading?: boolean }) {
  const base = "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 disabled:cursor-not-allowed";
  const sizes = { sm: "h-8 px-3 text-xs", md: "h-10 px-4 text-sm" };
  const variants = {
    primary: "bg-brand text-brand-fg hover:opacity-90",
    secondary: "bg-surface border border-line text-fg hover:bg-surface-2",
    ghost: "text-brand hover:bg-brand-soft",
    danger: "bg-danger-soft text-danger hover:opacity-90",
  };
  return (
    <button className={clsx(base, sizes[size], variants[variant], className)} disabled={loading || rest.disabled} {...rest}>
      {loading && <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />}
      {children}
    </button>
  );
}

export function Stat({ label, value, hint, tone, sub }: { label: string; value: React.ReactNode; hint?: React.ReactNode; tone?: "positive" | "danger" | "warning" | "brand"; sub?: React.ReactNode }) {
  const color = tone === "positive" ? "text-positive" : tone === "danger" ? "text-danger" : tone === "warning" ? "text-warning" : tone === "brand" ? "text-brand" : "text-fg";
  return (
    <div className="card min-w-0 p-4">
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className={clsx("mt-1 text-2xl font-semibold tabular", color)}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
      {hint && <div className="mt-2 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function Progress({ value, tone = "brand", className }: { value: number; tone?: "brand" | "positive" | "warning" | "danger"; className?: string }) {
  const colors = { brand: "bg-brand", positive: "bg-positive", warning: "bg-warning", danger: "bg-danger" };
  return (
    <div className={clsx("h-2 w-full overflow-hidden rounded-full bg-surface-2", className)} role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={100}>
      <div className={clsx("h-full rounded-full transition-all", colors[tone])} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

export function EmptyState({ icon, title, description, action }: { icon?: React.ReactNode; title: string; description?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="card flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon && <div className="mb-3 text-brand">{icon}</div>}
      <h3 className="text-base font-semibold">{title}</h3>
      {description && <p className="mt-1 max-w-md text-sm text-muted">{description}</p>}
      {action && <div className="mt-5 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse-soft rounded-lg bg-surface-2", className)} />;
}

export function Money({ value, className, signed, decimals }: { value: number | string | null | undefined; className?: string; signed?: boolean; decimals?: number }) {
  return <span className={clsx("tabular", className)}>{inr(value, { signed, decimals })}</span>;
}

export function Drawer({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title?: React.ReactNode; children?: React.ReactNode; wide?: boolean }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className={clsx("relative flex h-full w-full flex-col bg-surface shadow-xl", wide ? "max-w-3xl" : "max-w-xl")}>
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <h3 className="text-sm font-semibold">{title}</h3>
          <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-surface-2" aria-label="Close"><X size={18} /></button>
        </div>
        <div className="scrollbar-thin flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export function Field({ label, hint, children, className }: { label: string; hint?: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={clsx("block", className)}>
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

export const inputCls = "h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm text-fg placeholder:text-muted focus:border-brand focus:outline-none";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx(inputCls, props.className)} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={clsx(inputCls, props.className)} />;
}

export function Alert({ tone = "info", title, children }: { tone?: "info" | "warning" | "danger" | "positive"; title?: string; children?: React.ReactNode }) {
  const t = { info: "bg-info-soft text-info", warning: "bg-warning-soft text-warning", danger: "bg-danger-soft text-danger", positive: "bg-positive-soft text-positive" }[tone];
  return (
    <div className={clsx("rounded-xl px-4 py-3 text-sm", t)}>
      {title && <div className="font-semibold">{title}</div>}
      <div className={clsx(title && "mt-0.5", "text-fg/90")}>{children}</div>
    </div>
  );
}

export function Chip({ children, onClick, active }: { children: React.ReactNode; onClick?: () => void; active?: boolean }) {
  return (
    <button type="button" onClick={onClick} className={clsx("rounded-full border px-3 py-1 text-xs transition-colors", active ? "border-brand bg-brand-soft text-brand" : "border-line bg-surface text-fg hover:bg-surface-2")}>{children}</button>
  );
}

export function Table({ head, children, dense }: { head: React.ReactNode; children: React.ReactNode; dense?: boolean }) {
  return (
    <div className="scrollbar-thin overflow-x-auto">
      <table className={clsx("w-full text-left text-sm", dense && "text-xs")}>
        <thead className="text-[11px] uppercase tracking-wide text-muted"><tr className="border-b border-line">{head}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
export const th = "py-2 pr-4 font-medium";
export const td = "py-2.5 pr-4 align-top border-b border-line/60";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        <h1 className="text-xl font-semibold tracking-tight md:text-2xl">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}
