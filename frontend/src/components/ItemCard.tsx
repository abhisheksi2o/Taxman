"use client";
import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Api } from "@/lib/api";
import { dateTime, inr, KIND_LABEL, titleCase } from "@/lib/format";
import { Alert, Badge, Button, SeverityBadge } from "@/components/ui";
import { EvidenceList } from "@/components/EvidencePanel";

export function ItemCard({ item, caseId, onChanged, defaultOpen }: { item: any; caseId: string; onChanged: () => void; defaultOpen?: boolean }) {
  const router = useRouter();
  const [open, setOpen] = useState(!!defaultOpen);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const act = async (action: string, payload?: any) => {
    let note: string | undefined;
    if (action === "DISMISS") {
      note = prompt("Why are you dismissing this item? (recorded in the audit trail)") || undefined;
      if (!note) return;
    }
    setBusy(action);
    setErr(null);
    try {
      const res = await Api.resolve(caseId, item.id, action, payload, note);
      if (res.navigate) router.push(`/${res.navigate}`);
      onChanged();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };
  const resolved = item.status !== "OPEN";
  return (
    <div id={item.id} className={`card overflow-hidden ${resolved ? "opacity-80" : ""}`}>
      <button className="flex w-full items-start gap-3 px-5 py-4 text-left" onClick={() => setOpen((o) => !o)}>
        <span className="mt-1 text-muted">{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{item.title}</span><SeverityBadge severity={item.severity} /><Badge>{KIND_LABEL[item.kind] || item.kind}</Badge><Badge>{titleCase(item.category)}</Badge>{resolved && <Badge tone="positive">{item.status}</Badge>}</div>
          {!open && <p className="mt-1 line-clamp-2 text-sm text-muted">{item.description}</p>}
        </div>
        {item.impact_estimate !== null && item.impact_estimate !== undefined && <div className="text-right"><div className="text-[11px] uppercase tracking-wide text-muted">Impact</div><div className="tabular font-semibold">{inr(item.impact_estimate, { signed: true })}</div></div>}
      </button>
      {open && (
        <div className="space-y-4 border-t border-line px-5 py-4">
          <div><div className="mb-1 text-[11px] uppercase tracking-wide text-muted">Explanation</div><p className="text-sm">{item.description}</p></div>
          <div><div className="mb-1 text-[11px] uppercase tracking-wide text-muted">Evidence</div><EvidenceList sources={item.evidence} empty="Derived from your profile answers." /></div>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-xl bg-surface-2 p-3 text-sm"><div className="text-[11px] uppercase tracking-wide text-muted">Impact if known</div>{item.impact_estimate !== null ? <><span className="tabular font-semibold">{inr(item.impact_estimate, { signed: true })}</span><span className="block text-xs text-muted">{item.impact_note}</span></> : <span className="text-muted">Not quantifiable until confirmed</span>}</div>
            <div className="rounded-xl bg-surface-2 p-3 text-sm"><div className="text-[11px] uppercase tracking-wide text-muted">Required action</div>{item.required_action}</div>
          </div>
          {resolved ? (
            <div className="flex items-center justify-between text-xs text-muted"><span>{item.status === "DISMISSED" ? "Dismissed" : "Resolved"} {dateTime(item.resolved_at)} · {item.resolution_note}</span><Button size="sm" variant="ghost" onClick={async () => { await Api.reopen(caseId, item.id); onChanged(); }}>Reopen</Button></div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {item.suggested_resolutions.map((r: any, i: number) => <Button key={i} size="sm" variant={i === 0 ? "primary" : "secondary"} loading={busy === r.action} onClick={() => act(r.action, r.payload)}>{r.label}</Button>)}
              <Button size="sm" variant="ghost" loading={busy === "DISMISS"} onClick={() => act("DISMISS")}>Dismiss with reason</Button>
            </div>
          )}
          {err && <Alert tone="danger">{err}</Alert>}
          <p className="text-[11px] text-muted">Detected by {item.detected_by} · {dateTime(item.created_at)} · neutral finding: this item requires review, it does not imply wrongdoing.</p>
        </div>
      )}
    </div>
  );
}

