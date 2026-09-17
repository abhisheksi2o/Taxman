"use client";
import React, { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { FileText, RefreshCw, Trash2, Upload } from "lucide-react";
import { Api } from "@/lib/api";
import { useApp, useCaseData } from "@/lib/store";
import { dateTime, inr, titleCase } from "@/lib/format";
import { Alert, Badge, Button, Card, Drawer, Field, PageHeader, Progress, Select, Skeleton, StatusBadge, Table, td, th } from "@/components/ui";

function DocStatus({ s }: { s: string }) {
  const tone = s === "EXTRACTED" ? "positive" : s === "NEEDS_REVIEW" ? "warning" : s === "FAILED" ? "danger" : "neutral";
  return <Badge tone={tone}>{titleCase(s)}</Badge>;
}

function FieldsTable({ fields }: { fields: Record<string, any> }) {
  const rows = Object.entries(fields || {}).filter(([, v]) => v !== null && v !== undefined && typeof v !== "object");
  const lists = Object.entries(fields || {}).filter(([, v]) => Array.isArray(v) && v.length && typeof v[0] === "object");
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        {rows.map(([k, v]) => <div key={k}><dt className="text-[11px] uppercase tracking-wide text-muted">{titleCase(k)}</dt><dd className="tabular font-medium">{typeof v === "number" ? (Math.abs(v) >= 100 ? inr(v, { decimals: 2 }) : String(v)) : String(v)}</dd></div>)}
      </dl>
      {lists.map(([k, v]) => (
        <div key={k}>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-muted">{titleCase(k)} ({(v as any[]).length})</div>
          <div className="scrollbar-thin max-h-56 overflow-auto rounded-lg border border-line">
            <table className="w-full text-xs"><thead className="bg-surface-2 text-muted"><tr>{Object.keys((v as any[])[0]).slice(0, 7).map((h) => <th key={h} className="px-2 py-1 text-left font-medium">{titleCase(h)}</th>)}</tr></thead>
              <tbody>{(v as any[]).slice(0, 40).map((row, i) => <tr key={i} className="border-t border-line/60">{Object.keys((v as any[])[0]).slice(0, 7).map((h) => <td key={h} className="tabular px-2 py-1">{typeof row[h] === "number" ? (Math.abs(row[h]) >= 100 ? inr(row[h], { decimals: 2 }) : row[h]) : String(row[h] ?? "")}</td>)}</tr>)}</tbody></table>
          </div>
        </div>
      ))}
    </div>
  );
}

function DocumentsInner() {
  const { activeCaseId, touch } = useApp();
  const params = useSearchParams();
  const { data, loading, reload } = useCaseData((id) => Api.documents(id));
  const { data: reqs } = useCaseData((id) => Api.requirements(id));
  const [type, setType] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "positive" | "danger" | "warning"; text: string } | null>(null);
  const [openId, setOpenId] = useState<string | null>(params.get("doc"));
  const [detail, setDetail] = useState<any>(null);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (openId && activeCaseId) Api.document(activeCaseId, openId).then(setDetail).catch(() => setDetail(null));
    else setDetail(null);
  }, [openId, activeCaseId, data]);

  const upload = async (files: FileList | File[]) => {
    if (!activeCaseId) return;
    setBusy(true);
    setMsg(null);
    for (const f of Array.from(files)) {
      try {
        const res = await Api.upload(activeCaseId, f, type || undefined);
        const d = res.document;
        setMsg({ tone: d.status === "EXTRACTED" ? "positive" : "warning", text: `${d.filename}: detected ${titleCase(d.type)} · ${d.linked_entity_ids.length} value(s) extracted at ${Math.round(d.extraction_confidence * 100)}% confidence${d.flags.length ? " · " + d.flags.join("; ") : ""}` });
      } catch (e: any) {
        setMsg({ tone: "danger", text: `${f.name}: ${e.message}` });
      }
    }
    setBusy(false);
    touch();
    reload();
  };

  if (!activeCaseId) return <Alert tone="info">Start a return from the dashboard first.</Alert>;
  const docs = data?.documents || [];
  return (
    <div>
      <PageHeader title="Documents" subtitle="Upload → detect type → extract → normalise → attach to your profile with provenance. Nothing is added to income silently from AIS/26AS/bank statements – Astra proposes, you confirm." />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Upload" subtitle="PDF (text or scanned*), CSV, JSON, TXT, images · max 15 MB · encrypted at rest">
          <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files); }}
            className={`flex flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors ${drag ? "border-brand bg-brand-soft/50" : "border-line"}`}>
            <Upload className="text-brand" />
            <p className="mt-2 text-sm">Drag & drop Form 16, AIS, 26AS, bank / broker statements here</p>
            <p className="text-xs text-muted">or</p>
            <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
              <Select value={type} onChange={(e) => setType(e.target.value)} className="h-9 w-56 text-xs"><option value="">Auto-detect type</option>{(data?.types || []).map((t: any) => <option key={t.code} value={t.code}>{t.label}</option>)}</Select>
              <Button size="sm" onClick={() => inputRef.current?.click()} loading={busy}>Choose files</Button>
              <input ref={inputRef} type="file" multiple hidden onChange={(e) => e.target.files && upload(e.target.files)} accept=".pdf,.csv,.json,.txt,.png,.jpg,.jpeg" />
            </div>
            <p className="mt-3 text-[11px] text-muted">*Scanned PDFs / images use AI extraction only when an API key is configured; otherwise they are flagged for manual entry.</p>
          </div>
          {msg && <div className="mt-3"><Alert tone={msg.tone}>{msg.text}</Alert></div>}
        </Card>
        <Card title="Required documents" subtitle="From your profile">
          <ul className="space-y-2 text-sm">
            {(reqs?.items || []).filter((r: any) => r.kind === "DOCUMENT").map((r: any) => (
              <li key={r.code} className="flex items-start gap-2">
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${r.status === "SATISFIED" ? "bg-positive" : r.priority === "REQUIRED" ? "bg-danger" : r.priority === "RECOMMENDED" ? "bg-warning" : "bg-line"}`} />
                <span className={r.status === "SATISFIED" ? "text-muted line-through" : ""}>{r.label} <span className="text-[11px] text-muted">· {r.priority.toLowerCase()}</span></span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card className="mt-4" title={`Uploaded documents (${docs.length})`} padded={false}>
        {loading ? <div className="p-5"><Skeleton className="h-32" /></div> : docs.length === 0 ? <p className="p-5 text-sm text-muted">No documents yet.</p> : (
          <div className="px-5 pb-3">
            <Table head={<><th className={th}>Document</th><th className={th}>Type</th><th className={th}>Period</th><th className={th}>Confidence</th><th className={th}>Status</th><th className={th}>Values</th><th className={th}>Uploaded</th></>}>
              {docs.map((d: any) => (
                <tr key={d.id} className="cursor-pointer hover:bg-surface-2" onClick={() => setOpenId(d.id)}>
                  <td className={td}><span className="flex items-center gap-2"><FileText size={15} className="text-muted" /><span><span className="block font-medium">{d.filename}</span><span className="text-xs text-muted">{(d.size_bytes / 1024).toFixed(1)} KB · {d.extraction_method}</span></span></span></td>
                  <td className={td}><Badge tone="brand">{d.type_label}</Badge></td>
                  <td className={`${td} text-xs text-muted`}>{d.period_label || "—"}</td>
                  <td className={td}><div className="w-24"><Progress value={d.extraction_confidence * 100} tone={d.extraction_confidence >= 0.75 ? "positive" : "warning"} /><span className="text-[11px] text-muted">{Math.round(d.extraction_confidence * 100)}%</span></div></td>
                  <td className={td}><DocStatus s={d.status} />{d.flags.length > 0 && <span className="block max-w-xs truncate text-[11px] text-warning" title={d.flags.join("; ")}>{d.flags[0]}</span>}</td>
                  <td className={`${td} tabular`}>{d.linked_entity_ids.length}</td>
                  <td className={`${td} text-xs text-muted`}>{dateTime(d.uploaded_at)}</td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </Card>
      <Drawer open={!!openId} onClose={() => setOpenId(null)} title="Document" wide>
        {!detail ? <Skeleton className="h-40" /> : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div><h4 className="font-semibold">{detail.document.filename}</h4><p className="text-xs text-muted">{detail.document.type_label} · {detail.document.extraction_method} extraction · {Math.round(detail.document.extraction_confidence * 100)}% confidence · type detection {Math.round(detail.document.detected_type_confidence * 100)}%</p></div>
              <DocStatus s={detail.document.status} />
            </div>
            {detail.document.flags.length > 0 && <Alert tone="warning" title="Ambiguous extraction – please review"><ul className="list-disc pl-4">{detail.document.flags.map((f: string) => <li key={f}>{f}</li>)}</ul></Alert>}
            <FieldsTable fields={detail.document.extracted_fields} />
            {detail.linked_entities.length > 0 && (
              <div><div className="mb-1 text-[11px] uppercase tracking-wide text-muted">Values attached to your profile</div>
                <ul className="space-y-1 text-sm">{detail.linked_entities.map((e: any) => <li key={e.id} className="flex items-center justify-between rounded-lg border border-line px-3 py-2"><span>{e.employer_name || e.payer_name || e.deductor_name || e.description || e.lender || e.property_name}</span><span className="flex items-center gap-2"><StatusBadge status={e.status} /><span className="tabular font-medium">{inr((e.gross_salary || e.amount || e.tax_deducted || e.sale_consideration || e.interest_paid || e.annual_rent_received || e.gross_receipts)?.amount)}</span></span></li>)}</ul></div>
            )}
            {detail.related_items.length > 0 && <div><div className="mb-1 text-[11px] uppercase tracking-wide text-muted">Reconciliation items citing this document</div><ul className="space-y-1 text-sm">{detail.related_items.map((i: any) => <li key={i.id}><a className="text-brand hover:underline" href={`/reconciliation?item=${i.id}`}>{i.title}</a> <Badge tone={i.status === "OPEN" ? "warning" : "positive"}>{i.status}</Badge></li>)}</ul></div>}
            {detail.document.text_excerpt && <div><div className="mb-1 text-[11px] uppercase tracking-wide text-muted">View source (excerpt)</div><pre className="scrollbar-thin max-h-64 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">{detail.document.text_excerpt}</pre></div>}
            <div className="flex flex-wrap items-end gap-2 border-t border-line pt-4">
              <Field label="Re-process as"><Select id="reprocess-type" defaultValue={detail.document.type} className="h-9 w-56 text-xs">{(data?.types || []).map((t: any) => <option key={t.code} value={t.code}>{t.label}</option>)}</Select></Field>
              <Button size="sm" variant="secondary" onClick={async () => { const sel = (document.getElementById("reprocess-type") as HTMLSelectElement).value; await Api.reprocess(activeCaseId, detail.document.id, sel); touch(); reload(); setOpenId(null); }}><RefreshCw size={14} /> Re-process</Button>
              <Button size="sm" variant="danger" onClick={async () => { if (confirm("Delete this document and the values extracted from it?")) { await Api.deleteDocument(activeCaseId, detail.document.id); touch(); reload(); setOpenId(null); } }}><Trash2 size={14} /> Delete</Button>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}

export default function DocumentsPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><DocumentsInner /></Suspense>;
}
