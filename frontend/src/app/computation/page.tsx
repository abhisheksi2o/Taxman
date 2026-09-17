"use client";
import React, { useState } from "react";
import { Api } from "@/lib/api";
import { useApp, useCaseData } from "@/lib/store";
import { inr } from "@/lib/format";
import { Alert, Badge, Button, Card, PageHeader, Skeleton, Stat } from "@/components/ui";
import { LineTree } from "@/components/LineTree";
import { RegimeBars } from "@/components/charts";

export default function ComputationPage() {
  const { activeCaseId } = useApp();
  const { data, loading } = useCaseData((id) => Api.computation(id));
  const [regime, setRegime] = useState<string | null>(null);
  const [expandAll, setExpandAll] = useState(false);
  if (!activeCaseId) return <Alert tone="info">Start a return from the dashboard first.</Alert>;
  if (loading || !data) return <Skeleton className="h-96" />;
  const r = regime || data.working_regime;
  const rc = r === "OLD" ? data.old : data.new;
  const s = rc.summary;
  const cmp = data.comparison;
  const short: Record<string, string> = { taxable_income: "Taxable income", total_tax_liability: "Total tax", total_credits: "Taxes paid" };
  const barRows = cmp.rows.filter((x: any) => short[x.key]).map((x: any) => ({ label: short[x.key], old: x.old, new: x.new }));
  return (
    <div>
      <PageHeader title="Tax Computation" subtitle={<>Deterministic engine · rules <Badge tone="brand">{data.rules_version}</Badge> ({data.rules_status.toLowerCase()}) · as of {data.as_of} · {data.age_category.toLowerCase().replace("_", " ")} · {data.residential_status.toLowerCase().replace("_", " ")}</>}
        actions={<div className="flex rounded-lg border border-line p-1 text-sm">{["NEW", "OLD"].map((k) => <button key={k} onClick={() => setRegime(k)} className={`rounded-md px-3 py-1.5 ${r === k ? "bg-brand-soft font-medium text-brand" : "text-muted"}`}>{k === "NEW" ? "New regime (115BAC)" : "Old regime"}</button>)}</div>} />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <Stat label="Gross total income" value={inr(s.gross_total_income)} />
        <Stat label="Less: deductions" value={inr(s.total_deductions)} />
        <Stat label="Taxable income" value={inr(s.taxable_income)} />
        <Stat label="Total tax liability" value={inr(s.total_tax_liability)} sub={`Tax ${inr(s.tax_after_rebate)} + surcharge ${inr(s.surcharge)} + cess ${inr(s.cess)}`} />
        <Stat label={s.net_payable < 0 ? "Estimated refund" : "Estimated balance payable"} value={inr(Math.abs(s.net_payable))} tone={s.net_payable < 0 ? "positive" : s.net_payable > 0 ? "danger" : undefined} sub={`TDS ${inr(s.tds)} · paid ${inr(s.total_credits)}${s.total_interest > 0 ? ` · interest ${inr(s.total_interest)}` : ""}`} />
      </div>
      {data.open_items > 0 && <div className="mt-4"><Alert tone="warning">{data.open_items} reconciliation item(s) are open – these figures may change once they are resolved.</Alert></div>}
      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title={`${rc.label} – full computation`} subtitle="Every line is expandable. Click “Why?” on any figure to trace it to its rule, inputs and source documents." action={<Button size="sm" variant="ghost" onClick={() => setExpandAll((e) => !e)}>{expandAll ? "Collapse" : "Expand all"}</Button>} padded={false}>
          <div className="px-5 pb-4"><LineTree key={`${r}-${expandAll}`} lines={rc.lines} caseId={activeCaseId} regime={r} defaultOpen={expandAll} /></div>
        </Card>
        <div className="space-y-4">
          <Card title="Regime comparison" subtitle="Both regimes under the current versioned rules – Astra never chooses for you.">
            <RegimeBars rows={barRows} />
            <table className="mt-5 w-full text-sm">
              <thead><tr className="text-[11px] uppercase tracking-wide text-muted"><th className="py-1 text-left font-medium">Line</th><th className="py-1 pl-3 text-right font-medium">Old</th><th className="py-1 pl-3 text-right font-medium">New</th></tr></thead>
              <tbody>{cmp.rows.map((x: any) => <tr key={x.key} className="border-t border-line/60"><td className="py-1.5 pr-2">{x.label}</td><td className="tabular py-1.5 pl-3 text-right">{inr(x.old)}</td><td className="tabular py-1.5 pl-3 text-right">{inr(x.new)}</td></tr>)}</tbody>
            </table>
            <div className="mt-3 rounded-xl bg-surface-2 p-3 text-sm">{cmp.lower_tax_regime === "EQUAL" ? "Both regimes give the same liability." : <><span className="font-medium">{cmp.lower_tax_regime === "NEW" ? "New" : "Old"} regime is lower by {inr(cmp.difference)}</span> with the current data. Statutory default: {cmp.statutory_default.toLowerCase()}.</>}</div>
            <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-muted">{cmp.explanation.map((e: string, i: number) => <li key={i}>{e}</li>)}</ul>
          </Card>
          <Card title="Assumptions & warnings">
            {rc.warnings.length + data.validation_warnings.length === 0 && cmp.assumptions.length === 0 ? <p className="text-sm text-muted">None.</p> : (
              <ul className="space-y-1.5 text-xs">
                {[...data.validation_warnings, ...rc.warnings].map((w: string, i: number) => <li key={`w${i}`} className="rounded-md bg-warning-soft px-2 py-1 text-warning">{w}</li>)}
                {Array.from(new Set<string>([...rc.assumptions, ...cmp.assumptions])).map((a: string, i: number) => <li key={`a${i}`} className="rounded-md bg-surface-2 px-2 py-1 text-muted">{a}</li>)}
              </ul>
            )}
          </Card>
          <Card title="How credits were chosen" subtitle="TDS ledger – when sources conflict the 26AS figure is used">
            {rc.credit_ledger.length === 0 ? <p className="text-sm text-muted">No TDS recorded.</p> : <ul className="space-y-2 text-xs">{rc.credit_ledger.map((l: any, i: number) => <li key={i} className="rounded-lg border border-line p-2"><div className="flex justify-between"><span className="font-medium">{l.deductor} · s.{l.section}</span><span className="tabular">{inr(l.used)} <span className="text-muted">from {l.used_source}</span></span></div>{l.conflict && <div className="mt-1 text-warning">Sources differ: {l.candidates.map((c: any) => `${c.source} ${inr(c.amount)}`).join(" · ")}</div>}{l.provisional && <div className="mt-1 text-warning">Provisional – not yet in 26AS</div>}</li>)}</ul>}
          </Card>
        </div>
      </div>
      <p className="mt-4 text-[11px] text-muted">Estimates computed under {data.rules_version} for AY {data.assessment_year} (FY {data.financial_year}). Interest u/s 234A/B/C is an estimate as of the date shown. This is not tax advice; unresolved items may change the result.</p>
    </div>
  );
}
