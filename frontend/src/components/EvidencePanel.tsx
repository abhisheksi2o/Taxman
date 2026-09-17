"use client";
import React, { useEffect, useState } from "react";
import Link from "next/link";
import { FileText, HelpCircle } from "lucide-react";
import { Api } from "@/lib/api";
import { inr } from "@/lib/format";
import { Badge, Button, Drawer, Skeleton, StatusBadge } from "./ui";

export type Source = { label: string; source_type?: string; document_id?: string | null; reference?: string | null; amount?: number | null; confidence?: number | null; document_name?: string | null; entity_id?: string | null; period?: string | null };

export function SourceChip({ s }: { s: Source }) {
  const inner = (
    <span className="inline-flex max-w-full items-center gap-1 rounded-md border border-line bg-surface-2 px-2 py-1 text-[11px] text-fg" title={s.reference || s.label}>
      <FileText size={12} className="shrink-0 text-muted" />
      <span className="truncate">{s.label}</span>
      {s.amount !== undefined && s.amount !== null && <span className="tabular text-muted">· {inr(s.amount)}</span>}
      {typeof s.confidence === "number" && s.confidence < 1 && <span className="text-muted">· {Math.round(s.confidence * 100)}%</span>}
    </span>
  );
  return s.document_id ? <Link href={`/documents?doc=${s.document_id}`} className="hover:opacity-80">{inner}</Link> : inner;
}

export function EvidenceList({ sources, empty = "No source recorded" }: { sources: Source[]; empty?: string }) {
  if (!sources?.length) return <p className="text-xs text-muted">{empty}</p>;
  return <div className="flex flex-wrap gap-1.5">{sources.map((s, i) => <SourceChip key={i} s={s} />)}</div>;
}

/** Tree view used by "Why is this number here?" and the entity evidence drawer. */
export function EvidenceTree({ label, amount, status, sources, children }: { label: string; amount?: number | null; status?: string | null; sources: Source[]; children?: React.ReactNode }) {
  const empty = status === "CALCULATED" ? "Computed by the tax engine from the lines above (no document needed)" : status === "USER_ENTERED" || status === "USER_CONFIRMED" ? "Entered by you – no document attached" : "Derived from the lines above";
  return (
    <div className="rounded-xl border border-line p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-medium">{label}</div>
        <div className="flex items-center gap-2">
          {status && <StatusBadge status={status} />}
          {amount !== undefined && amount !== null && <span className="tabular text-sm font-semibold">{inr(amount)}</span>}
        </div>
      </div>
      <div className="mt-2 border-l-2 border-line pl-3">
        <div className="mb-1 text-[11px] uppercase tracking-wide text-muted">Sources</div>
        <EvidenceList sources={sources} empty={empty} />
        {children}
      </div>
    </div>
  );
}

export function WhyThisNumber({ caseId, regime, lineId, label = "Why is this number here?", compact }: { caseId: string; regime: string; lineId: string; label?: string; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    setData(null);
    Api.explain(caseId, regime, lineId).then(setData).catch((e) => setErr(e.message));
  }, [open, caseId, regime, lineId]);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="inline-flex items-center gap-1 text-[11px] font-medium text-brand hover:underline" title="Trace this figure to its rule, inputs and documents">
        <HelpCircle size={12} /> {compact ? "Why?" : label}
      </button>
      <Drawer open={open} onClose={() => setOpen(false)} title="Why is this number here?" wide>
        {err && <p className="text-sm text-danger">{err}</p>}
        {!data && !err && <div className="space-y-2"><Skeleton className="h-6 w-2/3" /><Skeleton className="h-20" /><Skeleton className="h-20" /></div>}
        {data && (
          <div className="space-y-4">
            <div>
              <div className="text-xs text-muted">{data.path?.map((p: any) => p.label).join(" › ")}</div>
              <div className="mt-1 flex items-baseline justify-between gap-3">
                <h4 className="text-base font-semibold">{data.line.label}</h4>
                <span className="tabular text-xl font-semibold">{inr(data.line.amount)}</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                {data.line.status && <StatusBadge status={data.line.status} />}
                {data.line.rule_ref && <Badge tone="brand">{data.line.rule_ref}</Badge>}
                <Badge tone={data.reconciliation_status === "OK" ? "positive" : "warning"}>{data.reconciliation_status === "OK" ? "Reconciled" : "⚠️ Reconciliation required"}</Badge>
              </div>
            </div>
            <p className="rounded-xl bg-surface-2 p-3 text-sm leading-relaxed">{data.narrative}</p>
            {data.line.formula && <div className="text-xs"><span className="text-muted">Formula · </span><code className="font-mono">{data.line.formula}</code></div>}
            {data.inputs?.length > 0 && (
              <div>
                <div className="mb-2 text-[11px] uppercase tracking-wide text-muted">Inputs</div>
                <div className="space-y-2">{data.inputs.map((i: any) => <EvidenceTree key={i.id} label={i.label} amount={i.amount} status={i.status} sources={i.sources} />)}</div>
              </div>
            )}
            {data.sources?.length > 0 && (
              <div>
                <div className="mb-2 text-[11px] uppercase tracking-wide text-muted">Direct sources</div>
                <EvidenceList sources={data.sources} />
              </div>
            )}
            {data.related_items?.length > 0 && (
              <div>
                <div className="mb-2 text-[11px] uppercase tracking-wide text-muted">Reconciliation items</div>
                <ul className="space-y-1 text-sm">{data.related_items.map((r: any) => <li key={r.id}><Link className="text-brand hover:underline" href={`/reconciliation?item=${r.id}`}>{r.title}</Link> <Badge tone={r.status === "OPEN" ? "warning" : "positive"}>{r.status}</Badge></li>)}</ul>
              </div>
            )}
            {data.line.notes?.length > 0 && <ul className="list-disc space-y-1 pl-5 text-xs text-muted">{data.line.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>}
            <p className="text-[11px] text-muted">Rules {data.rules_version}. Computed by the deterministic engine – not by AI.</p>
          </div>
        )}
      </Drawer>
    </>
  );
}

export function EntityEvidenceButton({ caseId, entityId, label = "View evidence" }: { caseId: string; entityId: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    if (open) Api.evidence(caseId, entityId).then(setData).catch(() => setData({ error: true }));
  }, [open, caseId, entityId]);
  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>{label}</Button>
      <Drawer open={open} onClose={() => setOpen(false)} title="Evidence trail">
        {!data && <Skeleton className="h-24" />}
        {data && !data.error && (
          <div className="space-y-3">
            <div className="flex items-center justify-between"><h4 className="font-semibold">{data.label}</h4><Badge tone={data.reconciliation_status === "OK" ? "positive" : "warning"}>{data.reconciliation_status === "OK" ? "Reconciled" : "⚠️ Review required"}</Badge></div>
            {data.fields.map((f: any) => <EvidenceTree key={f.field} label={f.field.replace(/_/g, " ")} amount={f.amount} status={f.status} sources={f.sources} />)}
            {data.reconciliation_items?.length > 0 && <div className="text-sm">{data.reconciliation_items.map((r: any) => <div key={r.id}><Link href={`/reconciliation?item=${r.id}`} className="text-brand hover:underline">{r.title}</Link> <Badge tone={r.status === "OPEN" ? "warning" : "positive"}>{r.status}</Badge></div>)}</div>}
          </div>
        )}
      </Drawer>
    </>
  );
}
