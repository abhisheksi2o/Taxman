"use client";
import React from "react";
import { Api } from "@/lib/api";
import { useApp, useCaseData } from "@/lib/store";
import { dateShort, timeShort } from "@/lib/format";
import { Alert, Badge, Card, PageHeader, Skeleton } from "@/components/ui";
import { EvidenceList } from "@/components/EvidencePanel";

export default function AuditPage() {
  const { activeCaseId } = useApp();
  const { data, loading } = useCaseData((id) => Api.audit(id));
  if (!activeCaseId) return <Alert tone="info">Start a return from the dashboard first.</Alert>;
  if (loading || !data) return <Skeleton className="h-96" />;
  const tone = (a: string) => (a === "USER" ? "brand" : a === "ASTRA" ? "positive" : "neutral");
  let lastDay = "";
  return (
    <div>
      <PageHeader title="Audit trail" subtitle="Every important action – uploads, extractions, reconciliations, confirmations, Astra answers – with the evidence used. No hidden chain-of-thought is stored." />
      <Card padded={false}>
        <ol className="divide-y divide-line">
          {data.events.map((e: any) => {
            const day = dateShort(e.ts);
            const showDay = day !== lastDay;
            lastDay = day;
            return (
              <React.Fragment key={e.id}>
                {showDay && <li className="bg-surface-2 px-5 py-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">{day}</li>}
                <li className="flex gap-4 px-5 py-3">
                  <span className="tabular w-16 shrink-0 pt-0.5 text-xs text-muted">{timeShort(e.ts)}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-sm"><Badge tone={tone(e.actor)}>{e.actor}</Badge><span>{e.summary}</span><span className="text-[11px] text-muted">{e.action}</span></div>
                    {e.evidence?.length > 0 && <div className="mt-1.5"><EvidenceList sources={e.evidence} /></div>}
                    {e.details && Object.keys(e.details).length > 0 && <details className="mt-1 text-[11px] text-muted"><summary className="cursor-pointer">details</summary><pre className="mt-1 whitespace-pre-wrap font-mono">{JSON.stringify(e.details, null, 1)}</pre></details>}
                  </div>
                </li>
              </React.Fragment>
            );
          })}
          {data.events.length === 0 && <li className="p-5 text-sm text-muted">No events yet.</li>}
        </ol>
      </Card>
    </div>
  );
}
