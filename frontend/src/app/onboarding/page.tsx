"use client";
import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Bot, Check } from "lucide-react";
import { Api } from "@/lib/api";
import { useApp, useCaseData } from "@/lib/store";
import { Alert, Button, Card, Chip, Field, Input, PageHeader, Select, Skeleton } from "@/components/ui";

const INCOME_SOURCES = [["SALARY", "Salary"], ["INTEREST", "Interest"], ["DIVIDEND", "Dividends"], ["CAPITAL_GAINS", "Capital gains"], ["RENTAL", "Rental income"], ["BUSINESS", "Business"], ["FREELANCE", "Freelance / profession"], ["FOREIGN", "Foreign income"], ["OTHER", "Other"]];

export default function OnboardingPage() {
  const { activeCaseId, supportedYears, touch, refreshCases, cases, currentYear, setActiveCase } = useApp();
  const { data, loading, reload } = useCaseData((id) => Api.onboarding(id));
  const [form, setForm] = useState<Record<string, any>>({});
  const [step, setStep] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (data) {
      setForm(data.profile);
      setStep((s) => s && data.steps.some((x: any) => x.id === s) ? s : data.next_step);
    }
  }, [data]);

  const createCase = async () => {
    const c = await Api.createCase(currentYear);
    await refreshCases();
    setActiveCase(c.id);
    touch();
  };
  if (!activeCaseId && !cases.length) {
    return <div className="mx-auto max-w-xl"><Card title="Let's prepare your tax return" subtitle="We start with the essentials only."><Button onClick={createCase}>Create my return workspace</Button></Card></div>;
  }
  if (loading || !data) return <Skeleton className="h-96" />;
  const current = data.steps.find((s: any) => s.id === step) || data.steps[0];
  const set = (k: string, v: any) => setForm((f) => ({ ...f, [k]: v }));
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const fields: string[] = current.id === "details" ? current.dynamic_fields.map((f: any) => f.name) : current.fields;
      const values: Record<string, any> = {};
      for (const f of fields) if (form[f] !== undefined) values[f] = form[f];
      const res = await Api.patchProfile(activeCaseId!, values, true);
      touch();
      const nextStep = res.next_step;
      setStep(nextStep);
      reload();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  const done = data.completed && !step;
  return (
    <div>
      <PageHeader title="Let's prepare your tax return" subtitle="Progressive onboarding – Astra decides what to ask next from your answers." />
      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Steps" className="lg:col-span-1">
          <ol className="space-y-1">
            {data.steps.map((s: any, i: number) => (
              <li key={s.id}>
                <button onClick={() => setStep(s.id)} className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm ${current.id === s.id ? "bg-brand-soft text-brand" : "hover:bg-surface-2"}`}>
                  <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs ${s.complete ? "bg-positive text-white" : "border border-line"}`}>{s.complete ? <Check size={12} /> : i + 1}</span>{s.title}
                </button>
              </li>
            ))}
          </ol>
          <div className="mt-4 flex gap-2 rounded-xl bg-surface-2 p-3 text-xs"><Bot size={16} className="mt-0.5 shrink-0 text-brand" /><span><span className="font-medium text-brand">Astra · </span>{data.astra_note}</span></div>
        </Card>
        <Card title={current.title} className="lg:col-span-2">
          {done && current.id === data.steps[data.steps.length - 1].id && <Alert tone="positive" title="Profile complete">Next: upload your Form 16, AIS and Form 26AS. <Link href="/documents" className="underline">Go to Documents →</Link></Alert>}
          <div className="mt-2 grid gap-4 md:grid-cols-2">
            {current.id === "assessment_year" && <Field label="Assessment year" hint="Rules are versioned per assessment year."><Select value={form.assessment_year || ""} onChange={(e) => set("assessment_year", e.target.value)}>{supportedYears.map((y) => <option key={y} value={y}>AY {y} (FY {Number(y.slice(0, 4)) - 1}-{y.slice(0, 4).slice(2)})</option>)}</Select></Field>}
            {current.id === "identity" && (<>
              <Field label="Full name"><Input value={form.name || ""} onChange={(e) => set("name", e.target.value)} /></Field>
              <Field label="PAN" hint={form.pan_masked ? `Stored: ${form.pan_masked}` : "Encrypted at rest, masked everywhere."}><Input value={form.pan || ""} onChange={(e) => set("pan", e.target.value.toUpperCase())} placeholder="ABCDE1234F" maxLength={10} /></Field>
              <Field label="Date of birth" hint="Decides senior-citizen limits."><Input type="date" value={form.date_of_birth || ""} onChange={(e) => set("date_of_birth", e.target.value)} /></Field>
              <Field label="Taxpayer type"><Select value={form.taxpayer_type || "INDIVIDUAL"} onChange={(e) => set("taxpayer_type", e.target.value)}><option value="INDIVIDUAL">Individual</option><option value="HUF">HUF</option></Select></Field>
              <Field label="Residential status"><Select value={form.residential_status || "RESIDENT"} onChange={(e) => set("residential_status", e.target.value)}><option value="RESIDENT">Resident</option><option value="RNOR">Resident but not ordinarily resident</option><option value="NON_RESIDENT">Non-resident</option></Select></Field>
            </>)}
            {current.id === "employment" && <Field label="Employment status" className="md:col-span-2"><div className="flex flex-wrap gap-2">{[["SALARIED", "Salaried"], ["SELF_EMPLOYED", "Self-employed / freelancer"], ["BOTH", "Both"], ["RETIRED", "Retired"], ["NOT_EMPLOYED", "Not employed"]].map(([v, l]) => <Chip key={v} active={form.employment_status === v} onClick={() => set("employment_status", v)}>{l}</Chip>)}</div></Field>}
            {current.id === "income_sources" && <Field label="Select every source that applies" className="md:col-span-2"><div className="flex flex-wrap gap-2">{INCOME_SOURCES.map(([v, l]) => { const on = (form.income_sources || []).includes(v); return <Chip key={v} active={on} onClick={() => set("income_sources", on ? form.income_sources.filter((x: string) => x !== v) : [...(form.income_sources || []), v])}>{l}</Chip>; })}</div></Field>}
            {current.id === "details" && current.dynamic_fields.map((f: any) => (
              <Field key={f.name} label={f.label}>
                {f.type === "number" && <Input type="number" min={0} value={form[f.name] ?? ""} onChange={(e) => set(f.name, e.target.value === "" ? null : Number(e.target.value))} />}
                {f.type === "select" && <Select value={form[f.name] || ""} onChange={(e) => set(f.name, e.target.value || null)}><option value="">Choose…</option>{f.options.map((o: string) => <option key={o} value={o}>{o.replace("_", "-").toLowerCase()}</option>)}</Select>}
                {f.type === "boolean" && <div className="flex gap-2"><Chip active={form[f.name] === true} onClick={() => set(f.name, true)}>Yes</Chip><Chip active={form[f.name] === false} onClick={() => set(f.name, false)}>No</Chip></div>}
              </Field>
            ))}
            {current.id === "history" && (<>
              <Field label="Did you file a return last year?"><div className="flex gap-2"><Chip active={form.filed_previous_return === true} onClick={() => set("filed_previous_return", true)}>Yes</Chip><Chip active={form.filed_previous_return === false} onClick={() => set("filed_previous_return", false)}>No</Chip></div></Field>
              <Field label="Regime preference" hint="Astra computes both; you choose at review time."><Select value={form.regime_preference || "UNDECIDED"} onChange={(e) => set("regime_preference", e.target.value)}><option value="UNDECIDED">Undecided – show me both</option><option value="NEW">New regime (115BAC)</option><option value="OLD">Old regime</option></Select></Field>
            </>)}
          </div>
          {err && <div className="mt-3"><Alert tone="danger">{err}</Alert></div>}
          <div className="mt-5 flex items-center justify-between">
            <span className="text-xs text-muted">Only essentials are asked now; everything else is progressive.</span>
            <Button onClick={save} loading={busy}>Save & continue</Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
