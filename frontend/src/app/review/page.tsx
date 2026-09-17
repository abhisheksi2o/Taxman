"use client";
import React, { useState } from "react";
import Link from "next/link";
import { CheckCircle2, CircleAlert, CircleX, Download, ShieldCheck } from "lucide-react";
import { Api } from "@/lib/api";
import { useApp, useCaseData } from "@/lib/store";
import { dateTime, inr, titleCase } from "@/lib/format";
import { Alert, Badge, Button, Card, PageHeader, Skeleton, StatusBadge } from "@/components/ui";
import { BlockBar, ReadinessGauge } from "@/components/charts";
import { LineTree } from "@/components/LineTree";

function CheckIcon({ s }: { s: string }) {
  if (s === "PASS") return <CheckCircle2 className="text-positive" size={20} />;
  if (s === "WARN") return <CircleAlert className="text-warning" size={20} />;
  return <CircleX className="text-danger" size={20} />;
}

export default function ReviewPage() {
  const { activeCaseId, touch } = useApp();
  const { data, loading, reload } = useCaseData((id) => Api.review(id));
  const [decl, setDecl] = useState<Record<string, boolean>>({});
  const [regime, setRegime] = useState<string | null>(null);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);
  if (!activeCaseId) return <Alert tone="info">Start a return from the dashboard first.</Alert>;
  if (loading || !data) return <Skeleton className="h-96" />;
  const r = data.readiness;
  const p = data.package;
  const selected = regime || data.review_state.selected_regime || p.regime;
  const summaryFor = (k: string) => (k === "OLD" ? p.comparison.rows.find((x: any) => x.key === "total_tax_liability").old : p.comparison.rows.find((x: any) => x.key === "total_tax_liability").new);
  const highOpen = p.open_items.filter((i: any) => i.severity === "HIGH").length;
  const confirmed = data.review_state.confirmed;
  const allDecl = p.declarations.every((d: any) => decl[d.code]);
  const confirm = async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await Api.confirmReview(activeCaseId, decl, selected, ack);
      setResult(res);
      touch();
      reload();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <PageHeader title="Return Review" subtitle="Before-You-File verification, then the complete return for your explicit confirmation. Nothing is ever submitted silently." />
      <Card title="RETURN READINESS" subtitle={r.summary}>
        <div className="flex flex-col items-start gap-6 md:flex-row md:items-center">
          <ReadinessGauge score={r.score} />
          <div>
            <div className="text-fg"><BlockBar score={r.score} /> <span className="tabular text-xl font-semibold">{r.score}%</span></div>
            <ul className="mt-2 space-y-0.5 text-sm"><li>Issues requiring review: <b>{r.issues_requiring_review}</b></li><li>Information missing: <b>{r.information_missing}</b></li><li>Calculation warnings: <b>{r.calculation_warnings}</b></li><li>Extracted values awaiting confirmation: <b>{r.unconfirmed_values}</b></li></ul>
            <div className="mt-3 flex flex-wrap gap-2"><Link href="/issues"><Button variant="secondary" size="sm">Review Issues</Button></Link><Link href="/computation"><Button variant="secondary" size="sm">View Evidence</Button></Link><a href="#final-review"><Button size="sm">Continue to Final Review</Button></a></div>
          </div>
        </div>
      </Card>
      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {r.checks.map((c: any) => (
          <div key={c.code} className="card p-4">
            <div className="flex items-start gap-2"><CheckIcon s={c.status} /><div><div className="font-medium">{c.label}</div><div className="text-xs text-muted">{c.question}</div></div></div>
            {c.findings.length > 0 && <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-muted">{c.findings.slice(0, 4).map((f: string, i: number) => <li key={i}>{f}</li>)}{c.findings.length > 4 && <li>+{c.findings.length - 4} more</li>}</ul>}
            {c.action && <div className="mt-2 text-xs text-brand">{c.action}</div>}
          </div>
        ))}
      </div>

      <h2 id="final-review" className="mt-10 text-lg font-semibold">Final return review</h2>
      <p className="mb-4 text-sm text-muted">Inspect every section. Values are labelled by how they entered the return: <StatusBadge status="EXTRACTED" /> <StatusBadge status="USER_CONFIRMED" /> <StatusBadge status="USER_ENTERED" /> <StatusBadge status="CALCULATED" /> <StatusBadge status="AI_SUGGESTED" /></p>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Personal details"><dl className="space-y-1.5 text-sm">{[["Name", p.personal_details.name], ["PAN", p.personal_details.pan_masked], ["Date of birth", p.personal_details.date_of_birth], ["Residential status", titleCase(p.personal_details.residential_status)], ["Type", titleCase(p.personal_details.taxpayer_type)], ["Email", p.personal_details.email], ["Phone", p.personal_details.phone]].map(([k, v]) => <div key={k as string} className="flex justify-between gap-3"><dt className="text-muted">{k}</dt><dd className="text-right font-medium">{(v as any) || "—"}</dd></div>)}</dl><Link href="/onboarding" className="mt-3 inline-block text-xs text-brand hover:underline">Edit</Link></Card>
        <Card title="Bank details" subtitle="Refund account">{p.bank_details.length === 0 ? <p className="text-sm text-danger">No bank account – add one in My Tax Profile.</p> : <ul className="space-y-1.5 text-sm">{p.bank_details.map((b: any) => <li key={b.id} className="flex justify-between"><span>{b.bank_name}<span className="block text-xs text-muted">{b.account_number_masked} · {b.ifsc || "IFSC —"}</span></span>{b.is_primary_for_refund && <Badge tone="positive">Primary</Badge>}</li>)}</ul>}</Card>
        <Card title="Applicable return" subtitle="Suggested ITR form"><div className="text-2xl font-semibold">{p.itr_form}</div><ul className="mt-2 list-disc pl-5 text-xs text-muted">{p.itr_form_reasons.map((x: string) => <li key={x}>{x}</li>)}</ul><div className="mt-3 text-xs text-muted">AY {p.assessment_year} · FY {p.financial_year} · rules {p.rules_version}</div><div className="mt-2 flex flex-wrap gap-1">{Object.entries(p.value_status_counts).map(([k, v]) => <Badge key={k}>{titleCase(k)}: {v as number}</Badge>)}</div></Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Income" padded={false}><div className="px-5 pb-3"><LineTree lines={Object.values(p.income).filter(Boolean) as any} caseId={activeCaseId} regime={selected} /></div></Card>
        <Card title="Deductions, tax and credits" padded={false}><div className="px-5 pb-3"><LineTree lines={[p.gross_total_income, p.deductions, p.taxable_income, ...p.tax, p.credits, p.interest, p.result].filter(Boolean)} caseId={activeCaseId} regime={selected} /></div></Card>
      </div>
      <Card className="mt-4" title="Regime selection" subtitle="Both computed under the versioned rules. You choose; Astra does not.">
        <div className="grid gap-3 md:grid-cols-2">
          {["NEW", "OLD"].map((k) => <label key={k} className={`flex cursor-pointer items-center justify-between rounded-xl border p-4 ${selected === k ? "border-brand bg-brand-soft/50" : "border-line"}`}><span className="flex items-center gap-3"><input type="radio" name="regime" checked={selected === k} onChange={() => setRegime(k)} disabled={confirmed} /><span><span className="block font-medium">{k === "NEW" ? "New regime (115BAC)" : "Old regime"}</span><span className="text-xs text-muted">{k === p.comparison.statutory_default ? "Statutory default" : "Requires opting in at filing"}</span></span></span><span className="tabular text-lg font-semibold">{inr(summaryFor(k))}</span></label>)}
        </div>
        <p className="mt-2 text-xs text-muted">{p.comparison.explanation[p.comparison.explanation.length - 1]}</p>
      </Card>
      {(p.open_items.length > 0 || p.resolved_items.length > 0) && (
        <Card className="mt-4" title={`Unresolved items (${p.open_items.length})`} subtitle="What still requires human confirmation">
          {p.open_items.length === 0 ? <p className="text-sm text-positive">All items resolved.</p> : <ul className="space-y-1.5 text-sm">{p.open_items.map((i: any) => <li key={i.id} className="flex items-center justify-between gap-3"><span><Link href={`/reconciliation?item=${i.id}`} className="text-brand hover:underline">{i.title}</Link></span><Badge tone={i.severity === "HIGH" ? "danger" : "warning"}>{i.severity}</Badge></li>)}</ul>}
          {p.resolved_items.length > 0 && <p className="mt-2 text-xs text-muted">{p.resolved_items.length} item(s) resolved or dismissed with notes – see the audit trail.</p>}
        </Card>
      )}
      <Card className="mt-4" title="Declarations & confirmation" subtitle="Explicit confirmation is required before a return package is prepared.">
        {confirmed && !result && <Alert tone="positive" title={`Confirmed ${dateTime(data.review_state.confirmed_at)} · ${data.review_state.selected_regime} regime`}>Package {data.review_state.return_package_id}. <a className="underline" href={`/api/cases/${activeCaseId}/review/package`}>Download return package (JSON)</a> · <button className="underline" onClick={async () => { await Api.resetReview(activeCaseId); setResult(null); reload(); }}>Reopen for changes</button></Alert>}
        {result && <Alert tone="positive" title="Return package prepared">{result.note} <a className="underline" href={`/api/cases/${activeCaseId}/review/package`}><Download size={12} className="inline" /> Download JSON</a></Alert>}
        {!confirmed && (
          <div className="mt-2 space-y-2">
            {p.declarations.map((d: any) => <label key={d.code} className="flex items-start gap-3 text-sm"><input type="checkbox" className="mt-1" checked={!!decl[d.code]} onChange={(e) => setDecl({ ...decl, [d.code]: e.target.checked })} />{d.text}</label>)}
            {highOpen > 0 && <label className="flex items-start gap-3 rounded-xl bg-danger-soft p-3 text-sm text-danger"><input type="checkbox" className="mt-1" checked={ack} onChange={(e) => setAck(e.target.checked)} />I acknowledge that {highOpen} high-priority item(s) remain unresolved and understand Astra recommends resolving them or seeking professional review first.</label>}
            {err && <Alert tone="danger">{err}</Alert>}
            <div className="flex flex-wrap items-center gap-3 pt-2"><Button onClick={confirm} loading={busy} disabled={!allDecl || (highOpen > 0 && !ack)}><ShieldCheck size={16} /> Confirm and prepare return package</Button><span className="text-xs text-muted">This prototype prepares a review package; it does not e-file anything.</span></div>
          </div>
        )}
        <p className="mt-4 text-[11px] text-muted">{p.disclaimer}</p>
      </Card>
    </div>
  );
}
