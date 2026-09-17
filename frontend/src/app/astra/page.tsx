"use client";
import React, { useEffect, useRef, useState } from "react";
import { Bot, Send, Trash2, User } from "lucide-react";
import { Api } from "@/lib/api";
import { useApp, useCaseData } from "@/lib/store";
import { timeShort } from "@/lib/format";
import { Alert, Badge, Button, Card, Chip, PageHeader, Skeleton } from "@/components/ui";
import { Markdown } from "@/components/Markdown";
import { EvidenceList } from "@/components/EvidencePanel";

export default function AstraPage() {
  const { activeCaseId, user, touch } = useApp();
  const { data, loading, reload } = useCaseData((id) => Api.astra(id));
  const [messages, setMessages] = useState<any[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (data) setMessages(data.messages || []); }, [data]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);
  if (!activeCaseId) return <Alert tone="info">Start a return from the dashboard first.</Alert>;
  const ask = async (question: string) => {
    if (!question.trim() || busy) return;
    setErr(null);
    setBusy(true);
    setMessages((m) => [...m, { role: "user", content: question, ts: new Date().toISOString() }]);
    setQ("");
    try {
      const res = await Api.ask(activeCaseId, question);
      setMessages((m) => [...m, { role: "assistant", content: res.answer, ts: new Date().toISOString(), mode: res.mode, tools: res.tools, evidence: res.evidence, grounding: res.grounding }]);
      touch();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex h-[calc(100dvh-9rem)] flex-col">
      <PageHeader title="Ask Astra" subtitle={<>Grounded tax investigator. Every figure comes from the deterministic engine and your documents; Astra explains, it never calculates. <Badge tone={user?.llm_enabled ? "positive" : "warning"}>{user?.llm_enabled ? `Claude (${data?.model || "LLM"}) + tools` : "Deterministic mode – no LLM key configured"}</Badge></>}
        actions={<Button variant="ghost" size="sm" onClick={async () => { await Api.clearAstra(activeCaseId); setMessages([]); reload(); }}><Trash2 size={14} /> Clear</Button>} />
      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-4">
        <Card title="Try asking" className="hidden lg:block">
          <div className="flex flex-col items-start gap-1.5">{(data?.suggested_questions || []).map((s: string) => <Chip key={s} onClick={() => ask(s)}>{s}</Chip>)}</div>
          <p className="mt-4 text-[11px] text-muted">Labels on each answer: <Badge>AI-generated explanation</Badge> <Badge tone="brand">Figures: deterministic engine</Badge>. Amounts that cannot be verified against the tax model are flagged.</p>
        </Card>
        <Card className="flex min-h-0 flex-col lg:col-span-3" padded={false}>
          <div className="scrollbar-thin flex-1 space-y-4 overflow-y-auto p-5">
            {loading && <Skeleton className="h-24" />}
            {!loading && messages.length === 0 && (
              <div className="rounded-2xl bg-surface-2 p-5 text-sm"><div className="flex items-center gap-2 font-medium"><Bot size={16} className="text-brand" /> Astra</div><p className="mt-2 text-muted">I can explain what income you have reported, why a number is what it is, what is missing, why sources disagree, and how the two regimes compare – always citing the evidence. What would you like to know?</p><div className="mt-3 flex flex-wrap gap-1.5 lg:hidden">{(data?.suggested_questions || []).slice(0, 5).map((s: string) => <Chip key={s} onClick={() => ask(s)}>{s}</Chip>)}</div></div>
            )}
            {messages.map((m, i) => m.role === "user" ? (
              <div key={i} className="flex justify-end"><div className="max-w-[85%] rounded-2xl rounded-br-sm bg-brand px-4 py-2.5 text-sm text-brand-fg"><div className="mb-0.5 flex items-center gap-1 text-[10px] opacity-80"><User size={11} /> You · {timeShort(m.ts)}</div>{m.content}</div></div>
            ) : (
              <div key={i} className="flex justify-start"><div className="max-w-[92%] rounded-2xl rounded-bl-sm border border-line bg-surface px-4 py-3">
                <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[10px] text-muted"><Bot size={12} className="text-brand" /> Astra · {timeShort(m.ts)} <Badge>{m.mode === "llm" ? "Claude + tools" : "Deterministic"}</Badge><Badge>AI-generated explanation</Badge>{m.grounding && (m.grounding.verified ? <Badge tone="positive">All amounts verified against the tax model</Badge> : <Badge tone="warning">Unverified: {m.grounding.unverified_amounts.join(", ")}</Badge>)}</div>
                <Markdown text={m.content} />
                {(m.evidence?.length > 0 || m.tools?.length > 0) && (
                  <div className="mt-3 border-t border-line pt-2">
                    {m.evidence?.length > 0 && <><div className="mb-1 text-[10px] uppercase tracking-wide text-muted">Grounded on</div><EvidenceList sources={m.evidence} /></>}
                    {m.tools?.length > 0 && <div className="mt-1.5 text-[10px] text-muted">Tools: {m.tools.map((t: any) => t.name).join(", ")}</div>}
                  </div>
                )}
              </div></div>
            ))}
            {busy && <div className="flex items-center gap-2 text-sm text-muted"><Bot size={16} className="text-brand animate-pulse-soft" /> Astra is checking the tax model…</div>}
            {err && <Alert tone="danger">{err}</Alert>}
            <div ref={endRef} />
          </div>
          <form className="flex gap-2 border-t border-line p-3" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about your income, a mismatch, a number, or what is missing…" className="h-11 flex-1 rounded-xl border border-line bg-surface px-4 text-sm focus:border-brand focus:outline-none" />
            <Button type="submit" loading={busy}><Send size={16} /> Ask</Button>
          </form>
        </Card>
      </div>
    </div>
  );
}
