"use client";
import React from "react";
import Link from "next/link";
import { Api } from "@/lib/api";
import { useApp, useCaseData } from "@/lib/store";
import { Alert, Card, PageHeader, Skeleton } from "@/components/ui";
import { ItemCard } from "@/components/ItemCard";

export default function IssuesPage() {
  const { activeCaseId, touch } = useApp();
  const { data, loading, reload } = useCaseData((id) => Api.issues(id));
  if (!activeCaseId) return <Alert tone="info">Start a return from the dashboard first.</Alert>;
  if (loading || !data) return <Skeleton className="h-96" />;
  const changed = () => { touch(); reload(); };
  const tiles = [["HIGH", "🔴 High priority", "danger"], ["MEDIUM", "🟠 Review", "warning"], ["LOW", "🟡 Review", "warning"], ["INFO", "🔵 Information", "info"]] as const;
  return (
    <div>
      <PageHeader title={data.headline} subtitle="Astra proactively checks every source against your return. Each alert carries an explanation, evidence, the impact if known and the action required." />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {tiles.map(([k, label, tone]) => <div key={k} className={`card p-4 ${data.counts[k] ? "" : "opacity-60"}`}><div className="text-xs text-muted">{label}</div><div className={`mt-1 text-2xl font-semibold text-${tone}`}>{data.counts[k]}</div></div>)}
      </div>
      <div className="mt-5 space-y-3">
        {data.alerts.map((a: any) => <ItemCard key={a.id} item={a} caseId={activeCaseId} onChanged={changed} />)}
        {data.alerts.length === 0 && <Card><p className="text-sm text-muted">No open alerts. <Link href="/documents" className="text-brand hover:underline">Upload more documents</Link> to widen the cross-check, or continue to <Link href="/review" className="text-brand hover:underline">Return Review</Link>.</p></Card>}
      </div>
      {data.resolved.length > 0 && <details className="mt-6"><summary className="cursor-pointer text-sm font-medium text-muted">Resolved & dismissed ({data.resolved.length})</summary><div className="mt-3 space-y-3">{data.resolved.map((a: any) => <ItemCard key={a.id} item={a} caseId={activeCaseId} onChanged={changed} />)}</div></details>}
    </div>
  );
}
