"use client";
import React, { useEffect, useState } from "react";
import { FlaskConical, Play } from "lucide-react";
import { Api } from "@/lib/api";
import { useApp, useCaseData } from "@/lib/store";
import { dateTime, inr } from "@/lib/format";
import { Alert, Badge, Button, Card, Chip, Field, Input, PageHeader, Select, Skeleton, Stat, Table, td, th } from "@/components/ui";
import { Markdown } from "@/components/Markdown";

function Result({ r }: { r: string }) {
  return <Badge tone={r === "PASS" ? "positive" : r === "PARTIAL" ? "warning" : "danger"}>{r}</Badge>;
}

export default function EvaluationPage() {
  const { user, activeCaseId, supportedYears, currentYear } = useApp();
  const [scenarios, setScenarios] = useState<any[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [seed, setSeed] = useState(42);
  const [ay, setAy] = useState(currentYear);
  const [qaMode, setQaMode] = useState("deterministic");
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<any>(null);
  const [runs, setRuns] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const { data: hidden } = useCaseData((id) => Api.evalCase(id).catch(() => null));
  useEffect(() => {
    if (!user?.dev_mode) return;
    Api.evalRun; // noop reference for tree-shaking clarity
    fetch("/api/dev/evaluation/scenarios", { credentials: "include" }).then((r) => r.json()).then((r) => setScenarios(r.scenarios || [])).catch(() => {});
    Api.evalRuns().then((r) => setRuns(r.runs)).catch(() => {});
  }, [user]);
  useEffect(() => setAy(currentYear), [currentYear]);
  if (!user?.dev_mode) return <Alert tone="warning">The evaluation screen is available to developer accounts when ASTRA_DEV_MODE is enabled.</Alert>;
  const start = async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await Api.evalRun({ seed, assessment_year: ay, scenarios: selected.length ? selected : null, include_qa: true, qa_mode: qaMode });
      setRun(res);
      Api.evalRuns().then((r) => setRuns(r.runs)).catch(() => {});
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <PageHeader title="AI Evaluation" subtitle="Developer-only benchmark: every demo scenario is generated, run through the real extraction → reconciliation → computation pipeline, and scored against its hidden issues. Astra's answers are checked for unverifiable amounts (hallucination detection)." />
      <Card title="Run a benchmark">
        <div className="flex flex-wrap gap-2">{scenarios.map((s) => <Chip key={s.code} active={selected.includes(s.code)} onClick={() => setSelected((sel) => sel.includes(s.code) ? sel.filter((x) => x !== s.code) : [...sel, s.code])}>{s.label}</Chip>)}<Chip active={selected.length === 0} onClick={() => setSelected([])}>All scenarios</Chip></div>
        <div className="mt-3 grid gap-3 sm:grid-cols-4">
          <Field label="Seed"><Input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></Field>
          <Field label="Assessment year"><Select value={ay} onChange={(e) => setAy(e.target.value)}>{supportedYears.map((y) => <option key={y}>{y}</option>)}</Select></Field>
          <Field label="Astra Q&A mode"><Select value={qaMode} onChange={(e) => setQaMode(e.target.value)}><option value="deterministic">Deterministic (offline)</option><option value="llm" disabled={!user.llm_enabled}>Claude + tools{user.llm_enabled ? "" : " (no key)"}</option></Select></Field>
          <div className="flex items-end"><Button onClick={start} loading={busy} className="w-full"><Play size={16} /> Run</Button></div>
        </div>
        {err && <div className="mt-3"><Alert tone="danger">{err}</Alert></div>}
      </Card>
      {hidden && (
        <Card className="mt-4" title={`Hidden test for the active case · ${hidden.label}`} subtitle={hidden.description}>
          <ul className="mb-3 list-disc pl-5 text-xs text-muted">{hidden.story.map((s: string) => <li key={s}>{s}</li>)}</ul>
          <Table head={<><th className={th}>Hidden issue</th><th className={th}>Detected</th><th className={th}>Evidence used</th><th className={`${th} text-right`}>Correct impact</th><th className={`${th} text-right`}>Astra impact</th></>}>
            {hidden.hidden_issues.map((h: any) => <tr key={h.id}><td className={td}><b>{h.id}</b> {h.title}<span className="block text-xs text-muted">{h.correct_answer}</span></td><td className={td}><Badge tone={h.detected ? "positive" : "danger"}>{h.detected ? "YES" : "NO"}</Badge>{h.detected_item && <span className="block text-xs text-muted">{h.detected_item.status}</span>}</td><td className={`${td} text-xs`}>{h.detected_item?.evidence.join("; ") || "—"}</td><td className={`${td} tabular text-right`}>{h.expected_impact !== null ? inr(h.expected_impact) : "—"}</td><td className={`${td} tabular text-right`}>{h.detected_item?.impact !== null && h.detected_item ? inr(h.detected_item.impact) : "—"}</td></tr>)}
          </Table>
        </Card>
      )}
      {busy && <div className="mt-4"><Skeleton className="h-40" /></div>}
      {run && (
        <div className="mt-4 space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Stat label="Detection rate" value={run.detection_rate !== null ? `${Math.round(run.detection_rate * 100)}%` : "—"} sub={`${run.detected}/${run.hidden_issues} hidden issues`} tone={run.detection_rate === 1 ? "positive" : "warning"} />
            <Stat label="Full passes" value={`${run.passed}/${run.hidden_issues}`} sub="detected + evidence + impact" />
            <Stat label="False positives" value={run.false_positives} sub="HIGH/MEDIUM items without a hidden issue" tone={run.false_positives ? "warning" : "positive"} />
            <Stat label="Astra Q&A" value={`${run.qa_passed}/${run.qa_total}`} sub="grounded & on-topic" />
            <Stat label="Run" value={run.scenarios} sub={`scenarios · seed ${run.seed} · ${dateTime(run.run_at)}`} />
          </div>
          {run.cases.map((c: any) => (
            <Card key={c.scenario} title={`${c.label} · ${c.detected}/${c.hidden_issue_count} detected · ${c.false_positives.length} false positive(s) · ${c.duration_ms} ms`} subtitle={c.description}>
              <ul className="mb-3 list-disc pl-5 text-xs text-muted">{c.story.map((s: string) => <li key={s}>{s}</li>)}</ul>
              {c.hidden_issues.length > 0 && (
                <div className="space-y-3">
                  {c.hidden_issues.map((h: any) => (
                    <div key={h.id} className="rounded-xl border border-line p-3 text-sm">
                      <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium">TEST CASE {h.id} · {h.title}</span><Result r={h.result} /></div>
                      <div className="mt-2 grid gap-3 md:grid-cols-2">
                        <div><div className="text-[11px] uppercase tracking-wide text-muted">Hidden issue</div><div>{h.hidden_issue}</div><div className="mt-1 text-[11px] uppercase tracking-wide text-muted">Correct answer</div><div>{h.correct_answer}</div></div>
                        <div><div className="text-[11px] uppercase tracking-wide text-muted">Detected</div><div>{h.detected ? <Badge tone="positive">YES</Badge> : <Badge tone="danger">NO</Badge>} {h.detected_item && <span className="text-xs text-muted">{h.detected_item.title}</span>}</div>
                          <div className="mt-1 text-[11px] uppercase tracking-wide text-muted">Evidence {h.evidence_ok ? "✓" : "✗"}</div><div className="text-xs">{h.evidence_used.join("; ") || "—"}<span className="block text-muted">expected: {h.expected_evidence.join(", ")}</span></div>
                          <div className="mt-1 grid grid-cols-2 gap-2"><div><div className="text-[11px] uppercase tracking-wide text-muted">Correct impact</div><div className="tabular">{h.correct_impact !== null ? inr(h.correct_impact) : "—"}</div></div><div><div className="text-[11px] uppercase tracking-wide text-muted">Astra impact {h.impact_ok ? "✓" : "✗"}</div><div className="tabular">{h.astra_impact !== null ? inr(h.astra_impact) : "—"}</div></div></div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {c.false_positives.length > 0 && <Alert tone="warning" title="False positives">{c.false_positives.map((f: any) => <div key={f.id}>{f.severity} · {f.title}</div>)}</Alert>}
              {c.qa.length > 0 && (
                <div className="mt-3">
                  <div className="mb-1 text-[11px] uppercase tracking-wide text-muted">Astra Q&A · {c.qa[0].mode}</div>
                  <div className="space-y-2">{c.qa.map((q: any, i: number) => <details key={i} className="rounded-lg border border-line p-3 text-sm"><summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2"><span className="font-medium">{q.question}</span><span className="flex gap-1"><Result r={q.result} />{q.hallucinated_amounts.length > 0 && <Badge tone="danger">Unverified: {q.hallucinated_amounts.join(", ")}</Badge>}{!q.mentions_ok && <Badge tone="warning">Missing: {q.must_mention.join(", ")}</Badge>}</span></summary><div className="mt-2 text-xs text-muted">Expected: {q.expected} · tools: {q.tools.map((t: any) => t.name).join(", ")}</div><div className="mt-2"><Markdown text={q.answer} /></div></details>)}</div>
                </div>
              )}
              <details className="mt-3 text-xs text-muted"><summary className="cursor-pointer">Documents & extraction ({c.documents.length})</summary><ul className="mt-1 list-disc pl-5">{c.documents.map((d: any) => <li key={d.filename}>{d.filename} · {d.type} · {Math.round(d.confidence * 100)}% · {d.status}{d.flags.length ? ` · ${d.flags.join("; ")}` : ""}</li>)}</ul></details>
            </Card>
          ))}
        </div>
      )}
      {runs.length > 0 && <Card className="mt-4" title="Previous runs"><ul className="space-y-1 text-sm">{runs.map((r) => <li key={r.id} className="flex flex-wrap justify-between gap-2"><span className="text-muted">{dateTime(r.ts)} · seed {r.results.seed} · {r.results.scenarios} scenario(s)</span><span>detection {Math.round((r.results.detection_rate || 0) * 100)}% · FP {r.results.false_positives} · Q&A {r.results.qa_passed}/{r.results.qa_total}</span><Button size="sm" variant="ghost" onClick={() => setRun(r.results)}>Open</Button></li>)}</ul></Card>}
      <p className="mt-4 flex items-center gap-1 text-[11px] text-muted"><FlaskConical size={12} /> Hidden test data is stored encrypted, separately from the taxpayer model, and is never exposed to Astra's tools.</p>
    </div>
  );
}
