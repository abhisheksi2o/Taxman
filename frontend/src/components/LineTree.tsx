"use client";
import React, { useState } from "react";
import clsx from "clsx";
import { ChevronDown, ChevronRight } from "lucide-react";
import { inr } from "@/lib/format";
import { EvidenceList, WhyThisNumber } from "./EvidencePanel";
import { Badge, StatusBadge } from "./ui";

export type Line = { id: string; label: string; amount: number; kind: string; formula?: string | null; rule_ref?: string | null; status?: string | null; sources?: any[]; children?: Line[]; notes?: string[]; entity_id?: string | null };

function LineRow({ line, depth, caseId, regime, defaultOpen }: { line: Line; depth: number; caseId: string; regime: string; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen && depth < 1);
  const hasChildren = !!line.children?.length;
  const isResult = line.kind === "result";
  const isSub = line.kind === "subtotal" || line.kind === "tax" || line.kind === "credit" || line.kind === "interest";
  const negative = line.amount < 0 || line.kind === "deduction";
  return (
    <div className={clsx(depth === 0 && "border-b border-line/70 last:border-0")}>
      <div className={clsx("flex items-start gap-2 py-2", depth > 0 && "pl-4", isResult && "bg-brand-soft/60 rounded-lg px-2")} style={{ marginLeft: depth > 0 ? (depth - 1) * 16 : 0 }}>
        <button type="button" onClick={() => setOpen((o) => !o)} className={clsx("mt-0.5 rounded p-0.5 text-muted hover:bg-surface-2", !hasChildren && !line.formula && !line.sources?.length && "invisible")} aria-label={open ? "Collapse" : "Expand"}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className={clsx("text-sm", (isResult || (isSub && depth === 0)) && "font-semibold", line.kind === "info" && "text-muted")}>{line.label}</span>
            {line.status && depth > 0 && <StatusBadge status={line.status} />}
            {line.kind === "info" && <Badge>info</Badge>}
          </div>
          {open && (
            <div className="mt-1 space-y-1.5 text-xs text-muted">
              {line.formula && <div><span className="uppercase tracking-wide text-[10px]">Formula</span> · <code className="font-mono text-fg/80">{line.formula}</code></div>}
              {line.rule_ref && <div><span className="uppercase tracking-wide text-[10px]">Rule</span> · {line.rule_ref}</div>}
              {line.sources && line.sources.length > 0 && <EvidenceList sources={line.sources} />}
              {line.notes?.map((n, i) => <div key={i} className="rounded-md bg-warning-soft px-2 py-1 text-warning">{n}</div>)}
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-0.5">
          <span className={clsx("tabular text-sm", (isResult || (isSub && depth === 0)) && "font-semibold", negative && !isResult && "text-muted", line.kind === "info" && "text-muted")}>{inr(line.amount)}</span>
          <WhyThisNumber caseId={caseId} regime={regime} lineId={line.id} compact />
        </div>
      </div>
      {open && hasChildren && <div>{line.children!.map((c) => <LineRow key={c.id} line={c} depth={depth + 1} caseId={caseId} regime={regime} defaultOpen={defaultOpen} />)}</div>}
    </div>
  );
}

export function LineTree({ lines, caseId, regime, defaultOpen = false }: { lines: Line[]; caseId: string; regime: string; defaultOpen?: boolean }) {
  return <div>{lines.map((l) => <LineRow key={l.id} line={l} depth={0} caseId={caseId} regime={regime} defaultOpen={defaultOpen} />)}</div>;
}
