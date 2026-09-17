"use client";
import React, { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { inr } from "@/lib/format";

/** Reads the validated categorical palette from CSS tokens so charts follow light/dark mode. */
export function usePalette(): string[] {
  const [colors, setColors] = useState<string[]>(["#3457d5", "#0f9d8a", "#d97706", "#8b5cf6", "#c2417a"]);
  useEffect(() => {
    const read = () => {
      const cs = getComputedStyle(document.documentElement);
      const c = [1, 2, 3, 4, 5].map((i) => cs.getPropertyValue(`--chart-${i}`).trim()).filter(Boolean);
      if (c.length === 5) setColors(c);
    };
    read();
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", read);
    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      mq.removeEventListener("change", read);
      obs.disconnect();
    };
  }, []);
  return colors;
}

function TooltipBox({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow">
      {label && <div className="mb-1 font-medium">{label}</div>}
      {payload.map((p: any, i: number) => (
        <div key={i} className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: p.color || p.payload?.fill }} /><span className="text-muted">{p.name}</span><span className="tabular ml-auto font-medium">{inr(p.value)}</span></div>
      ))}
    </div>
  );
}

export function IncomeDonut({ data }: { data: { label: string; amount: number }[] }) {
  const palette = usePalette();
  const total = data.reduce((s, d) => s + Math.max(0, d.amount), 0);
  if (!data.length || total <= 0) return <p className="text-sm text-muted">No income recorded yet.</p>;
  const rows = data.map((d) => ({ name: d.label, value: Math.max(0, d.amount) }));
  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row">
      <div className="h-44 w-44 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={rows} dataKey="value" nameKey="name" innerRadius={52} outerRadius={80} paddingAngle={2} stroke="var(--surface)" strokeWidth={2} isAnimationActive={false}>
              {rows.map((_, i) => <Cell key={i} fill={palette[i % palette.length]} />)}
            </Pie>
            <Tooltip content={<TooltipBox />} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="min-w-0 flex-1 space-y-1.5 text-sm">
        {rows.map((r, i) => (
          <li key={r.name} className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: palette[i % palette.length] }} />
            <span className="min-w-0 truncate text-muted">{r.name}</span>
            <span className="tabular ml-auto font-medium">{inr(r.value)}</span>
            <span className="tabular w-10 text-right text-xs text-muted">{Math.round((r.value / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RegimeBars({ rows }: { rows: { label: string; old: number; new: number }[] }) {
  const palette = usePalette();
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 8, right: 8, left: 8, bottom: 8 }} barGap={2}>
          <CartesianGrid vertical={false} stroke="var(--line)" />
          <XAxis dataKey="label" interval={0} tick={{ fontSize: 11, fill: "var(--muted)" }} axisLine={false} tickLine={false} />
          <YAxis tickFormatter={(v) => inr(v, { compact: true })} tick={{ fontSize: 11, fill: "var(--muted)" }} axisLine={false} tickLine={false} width={76} />
          <Tooltip content={<TooltipBox />} cursor={{ fill: "var(--surface-2)" }} />
          <Bar dataKey="old" name="Old regime" fill={palette[4]} radius={[4, 4, 0, 0]} isAnimationActive={false} />
          <Bar dataKey="new" name="New regime" fill={palette[0]} radius={[4, 4, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-1 flex justify-center gap-4 text-xs text-muted">
        <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: palette[4] }} />Old regime</span>
        <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: palette[0] }} />New regime</span>
      </div>
    </div>
  );
}

export function ReadinessGauge({ score, size = 132 }: { score: number; size?: number }) {
  const r = size / 2 - 10;
  const c = 2 * Math.PI * r;
  const pctv = Math.max(0, Math.min(100, score));
  const tone = pctv >= 85 ? "var(--positive)" : pctv >= 60 ? "var(--warning)" : "var(--danger)";
  return (
    <div className="relative" style={{ width: size, height: size }} role="img" aria-label={`Return readiness ${pctv}%`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--line)" strokeWidth={10} fill="none" />
        <circle cx={size / 2} cy={size / 2} r={r} stroke={tone} strokeWidth={10} fill="none" strokeLinecap="round" strokeDasharray={`${(pctv / 100) * c} ${c}`} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="tabular text-2xl font-semibold">{pctv}%</span>
        <span className="text-[10px] uppercase tracking-wide text-muted">ready</span>
      </div>
    </div>
  );
}

export function BlockBar({ score }: { score: number }) {
  const filled = Math.round(score / 10);
  return <span className="font-mono text-lg tracking-tight" aria-hidden>{"█".repeat(filled)}{"░".repeat(10 - filled)}</span>;
}
