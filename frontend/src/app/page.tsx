"use client";
import React, { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Bot, FileUp, Sparkles, Wand2 } from "lucide-react";
import { Api } from "@/lib/api";
import { useApp, useCaseData } from "@/lib/store";
import { dateTime, inr, pct, SEVERITY_META, timeShort } from "@/lib/format";
import { Alert, Badge, Button, Card, PageHeader, SeverityBadge, Skeleton, Stat } from "@/components/ui";
import { IncomeDonut, ReadinessGauge } from "@/components/charts";
import { DemoGenerator } from "@/components/DemoGenerator";

function StartScreen() {
  const router = useRouter();
  const { currentYear, refreshCases, setActiveCase, touch } = useApp();
  const [demo, setDemo] = useState(false);
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    try {
      const c = await Api.createCase(currentYear);
      await refreshCases();
      setActiveCase(c.id);
      touch();
      router.push("/onboarding");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mx-auto max-w-3xl">
      <div className="card p-8 text-center md:p-12">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-brand text-brand-fg"><Sparkles size={22} /></div>
        <h1 className="mt-5 text-2xl font-semibold md:text-3xl">Let&apos;s prepare your tax return</h1>
        <p className="mx-auto mt-2 max-w-xl text-sm text-muted">Astra asks only for the essentials, then decides what documents and details are needed next. Upload Form 16, AIS, 26AS, bank and broker statements – every figure stays traceable to its source.</p>
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <Button onClick={start} loading={busy}>Start my return <ArrowRight size={16} /></Button>
          <Button variant="secondary" onClick={() => setDemo(true)}><Wand2 size={16} /> Generate demo taxpayer</Button>
        </div>
        <div className="mt-8 grid gap-3 text-left text-xs text-muted sm:grid-cols-3">
          <div className="rounded-xl bg-surface-2 p-3"><div className="font-medium text-fg">Deterministic engine</div>Versioned rules per assessment year; the AI never computes tax.</div>
          <div className="rounded-xl bg-surface-2 p-3"><div className="font-medium text-fg">Reconciliation</div>Form 16 · 16A · AIS · TIS · 26AS · bank · broker · previous ITR.</div>
          <div className="rounded-xl bg-surface-2 p-3"><div className="font-medium text-fg">Evidence for everything</div>Click any number to see why it is there.</div>
        </div>
      </div>
      <DemoGenerator open={demo} onClose={() => setDemo(false)} />
    </div>
  );
}

export default function DashboardPage() {
  const { cases, activeCaseId, loading } = useApp();
  const { data, error, loading: busy } = useCaseData((id) => Api.dashboard(id));
  const [demo, setDemo] = useState(false);
  if (loading) return <Skeleton className="h-64" />;
  if (!cases.length || !activeCaseId) return <StartScreen />;
  if (error) return <Alert tone="danger">{error}</Alert>;
  if (busy || !data) return <div className="space-y-4"><Skeleton className="h-10 w-1/2" /><div className="grid gap-4 md:grid-cols-4"><Skeleton className="h-24" /><Skeleton className="h-24" /><Skeleton className="h-24" /><Skeleton className="h-24" /></div><Skeleton className="h-64" /></div>;
  const k = data.kpis;
  const r = data.readiness;
  const position = k.position === "refund" ? { label: "Estimated refund", value: inr(-k.net_payable), tone: "positive" as const } : k.position === "payable" ? { label: "Estimated balance payable", value: inr(k.net_payable), tone: "danger" as const } : { label: "Estimated balance", value: inr(0), tone: undefined };
  return (
    <div>
      <PageHeader title={data.case.taxpayer_name ? `Hello, ${data.case.taxpayer_name.split(" ")[0]}` : "Dashboard"} subtitle={<>AY {data.case.assessment_year} · working regime <Badge tone="brand">{data.regime === "NEW" ? "New (115BAC)" : "Old"}</Badge> · rules {data.rules_version} · updated {dateTime(data.case.updated_at)}</>}
        actions={<><Link href="/documents"><Button variant="secondary"><FileUp size={16} /> Upload document</Button></Link><Link href="/astra"><Button><Bot size={16} /> Ask Astra</Button></Link><Button variant="ghost" onClick={() => setDemo(true)}><Wand2 size={16} /> Demo</Button></>} />
      {!data.case.onboarding_completed && <div className="mb-4"><Alert tone="warning" title="Your profile is incomplete">Astra needs a few essentials to know which documents to ask for. <Link className="underline" href="/onboarding">Continue onboarding →</Link></Alert></div>}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <Stat label="Gross total income" value={inr(k.gross_total_income)} sub={`Effective rate ${pct(k.effective_rate, 1)}`} />
        <Stat label="Taxable income" value={inr(k.taxable_income)} sub="After deductions, rounded u/s 288A" />
        <Stat label="Total tax liability" value={inr(k.total_tax)} sub="Incl. cess · deterministic engine" />
        <Stat label="Taxes already paid" value={inr(k.credits)} sub="TDS · TCS · advance tax" />
        <Stat label={position.label} value={position.value} tone={position.tone} sub={data.comparison.lower !== "EQUAL" ? `${data.comparison.lower === "NEW" ? "New" : "Old"} regime lower by ${inr(data.comparison.difference)}` : "Both regimes equal"} />
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Return readiness" subtitle="Before-You-File check" action={<Link href="/review" className="text-xs font-medium text-brand hover:underline">Open review →</Link>} className="lg:col-span-1">
          <div className="flex items-center gap-5">
            <ReadinessGauge score={r.score} />
            <ul className="space-y-1.5 text-sm">
              <li className="flex justify-between gap-4"><span className="text-muted">Issues requiring review</span><span className="font-medium">{r.issues_requiring_review}</span></li>
              <li className="flex justify-between gap-4"><span className="text-muted">Information missing</span><span className="font-medium">{r.information_missing}</span></li>
              <li className="flex justify-between gap-4"><span className="text-muted">Calculation warnings</span><span className="font-medium">{r.calculation_warnings}</span></li>
              <li className="flex justify-between gap-4"><span className="text-muted">Values to confirm</span><span className="font-medium">{r.unconfirmed_values}</span></li>
            </ul>
          </div>
          {r.blocking.length > 0 && <p className="mt-3 text-xs text-danger">Blocking: {r.blocking.join(", ")}</p>}
        </Card>
        <Card title={data.alerts.headline} subtitle="Proactive findings from cross-source reconciliation – neutral, evidence-backed, never an accusation." action={<Link href="/issues" className="text-xs font-medium text-brand hover:underline">All issues →</Link>} className="lg:col-span-2">
          {data.alerts.items.length === 0 ? <p className="text-sm text-muted">No open items. Upload more documents (AIS, 26AS, bank statements) to widen the cross-check.</p> : (
            <ul className="divide-y divide-line">
              {data.alerts.items.map((a: any) => (
                <li key={a.id} className="flex items-start gap-3 py-2.5">
                  <span className="mt-0.5 text-base" aria-hidden>{SEVERITY_META[a.severity]?.icon}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-medium">{a.title}</span><SeverityBadge severity={a.severity} /></div>
                    <div className="mt-0.5 text-xs text-muted">{a.required_action}</div>
                  </div>
                  <div className="text-right">
                    {a.impact !== null && <div className="tabular text-sm font-medium">{inr(a.impact, { signed: true })}</div>}
                    <Link href={`/reconciliation?item=${a.id}`} className="text-xs text-brand hover:underline">Review</Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Income composition" subtitle={`${data.regime === "NEW" ? "New" : "Old"} regime · after set-off`}><IncomeDonut data={data.composition} /></Card>
        <Card title="Documents" subtitle={`${data.documents.count} uploaded · ${data.documents.needs_review} need review`} action={<Link href="/documents" className="text-xs font-medium text-brand hover:underline">Manage →</Link>}>
          {data.documents.required_missing.length ? <ul className="space-y-1.5 text-sm">{data.documents.required_missing.map((d: string) => <li key={d} className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-warning" />{d} <Badge tone="warning">required</Badge></li>)}</ul> : <p className="text-sm text-positive">All required documents are in.</p>}
        </Card>
        <Card title="Recent activity" subtitle="Audit trail" action={<Link href="/audit" className="text-xs font-medium text-brand hover:underline">Full trail →</Link>}>
          <ul className="space-y-2 text-sm">
            {data.recent_activity.map((e: any) => <li key={e.id} className="flex min-w-0 gap-3"><span className="tabular w-16 shrink-0 text-xs text-muted">{timeShort(e.ts)}</span><span className="min-w-0 flex-1"><span className="block truncate">{e.summary}</span><span className="text-[11px] text-muted">{e.actor}</span></span></li>)}
          </ul>
        </Card>
      </div>
      <DemoGenerator open={demo} onClose={() => setDemo(false)} />
    </div>
  );
}
