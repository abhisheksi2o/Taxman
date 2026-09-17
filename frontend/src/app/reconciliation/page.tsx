"use client";
import React, { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { RefreshCw } from "lucide-react";
import { Api } from "@/lib/api";
import { useApp, useCaseData } from "@/lib/store";
import { dateTime, inr, KIND_LABEL } from "@/lib/format";
import { Alert, Badge, Button, Card, Chip, PageHeader, Skeleton } from "@/components/ui";
import { ItemCard } from "@/components/ItemCard";

function ReconciliationInner() {
  const { activeCaseId, touch } = useApp();
  const params = useSearchParams();
  const focus = params.get("item");
  const { data, loading, reload } = useCaseData((id) => Api.reconciliation(id));
  const [kind, setKind] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (focus && data) setTimeout(() => document.getElementById(focus)?.scrollIntoView({ behavior: "smooth", block: "center" }), 100);
  }, [focus, data]);
  if (!activeCaseId) return <Alert tone="info">Start a return from the dashboard first.</Alert>;
  if (loading || !data) return <Skeleton className="h-96" />;
  const items = data.items.filter((i: any) => !kind || i.kind === kind);
  const open = items.filter((i: any) => i.status === "OPEN");
  const done = items.filter((i: any) => i.status !== "OPEN");
  const changed = () => { touch(); reload(); };
  const m = data.matrix;
  return (
    <div>
      <PageHeader title="Reconciliation" subtitle={`Cross-source comparison of Form 16 · 16A · AIS · TIS · 26AS · bank · broker · your entries. Last run ${dateTime(data.last_run)}.`}
        actions={<Button variant="secondary" loading={busy} onClick={async () => { setBusy(true); try { await Api.runReconciliation(activeCaseId); changed(); } finally { setBusy(false); } }}><RefreshCw size={16} /> Run reconciliation</Button>} />
      <Card title="Source matrix" subtitle="Amounts per category as reported by each source vs what is in the return" padded={false}>
        <div className="scrollbar-thin overflow-x-auto px-5 pb-4">
          <table className="w-full text-sm">
            <thead><tr className="text-[11px] uppercase tracking-wide text-muted"><th className="py-2 pr-4 text-left font-medium">Category</th>{m.sources.map((s: any) => <th key={s.code} className={`py-2 pr-4 text-right font-medium ${s.code === "RETURN" ? "text-brand" : ""}`}>{s.label}</th>)}<th className="py-2 text-right font-medium">Status</th></tr></thead>
            <tbody>{m.rows.map((r: any) => <tr key={r.category} className="border-t border-line/60"><td className="py-2 pr-4 font-medium">{r.category}</td>{m.sources.map((s: any) => <td key={s.code} className={`tabular py-2 pr-4 text-right ${s.code === "RETURN" ? "font-semibold" : ""} ${r.values[s.code] ? "" : "text-muted"}`}>{r.values[s.code] ? inr(r.values[s.code]) : "·"}</td>)}<td className="py-2 text-right"><Badge tone={r.status === "OK" ? "positive" : "warning"}>{r.status === "OK" ? "Agrees" : "Review"}</Badge></td></tr>)}
              {m.rows.length === 0 && <tr><td colSpan={m.sources.length + 2} className="py-4 text-center text-muted">Upload documents to populate the matrix.</td></tr>}</tbody>
          </table>
        </div>
      </Card>
      <div className="mt-6 mb-3 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{open.length} open · {done.length} resolved</span>
        <span className="mx-2 h-4 w-px bg-line" />
        <Chip active={!kind} onClick={() => setKind(null)}>All</Chip>
        {Object.entries(KIND_LABEL).map(([k, l]) => <Chip key={k} active={kind === k} onClick={() => setKind(k)}>{l}</Chip>)}
      </div>
      <div className="space-y-3">
        {open.map((i: any) => <ItemCard key={i.id} item={i} caseId={activeCaseId} onChanged={changed} defaultOpen={i.id === focus} />)}
        {open.length === 0 && <Alert tone="positive" title="Nothing to review">All sources agree with the return as it stands. Upload more statements to widen the check.</Alert>}
      </div>
      {done.length > 0 && <details className="mt-6"><summary className="cursor-pointer text-sm font-medium text-muted">Resolved & dismissed ({done.length})</summary><div className="mt-3 space-y-3">{done.map((i: any) => <ItemCard key={i.id} item={i} caseId={activeCaseId} onChanged={changed} />)}</div></details>}
    </div>
  );
}

export default function ReconciliationPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><ReconciliationInner /></Suspense>;
}
