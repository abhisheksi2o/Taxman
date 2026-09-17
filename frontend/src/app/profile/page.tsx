"use client";
import React, { useState } from "react";
import Link from "next/link";
import { Check, Pencil, Plus, Trash2 } from "lucide-react";
import { Api } from "@/lib/api";
import { useApp, useCaseData } from "@/lib/store";
import { dateShort, inr, titleCase } from "@/lib/format";
import { Alert, Badge, Button, Card, Drawer, Field, Input, PageHeader, Select, Skeleton, StatusBadge, Table, td, th } from "@/components/ui";
import { EntityEvidenceButton, EvidenceList } from "@/components/EvidencePanel";

type FieldDef = { name: string; label: string; type?: "text" | "number" | "date" | "select"; options?: string[]; required?: boolean };
const FORMS: Record<string, { title: string; fields: FieldDef[] }> = {
  SALARY: { title: "Salary (employer)", fields: [{ name: "employer_name", label: "Employer", required: true }, { name: "employer_tan", label: "Employer TAN" }, { name: "gross_salary", label: "Gross salary u/s 17", type: "number", required: true }, { name: "exempt_allowances", label: "Exempt allowances u/s 10", type: "number" }, { name: "professional_tax", label: "Professional tax", type: "number" }, { name: "employer_nps_contribution", label: "Employer NPS (80CCD(2))", type: "number" }, { name: "tds", label: "TDS as per Form 16", type: "number" }, { name: "basic_salary", label: "Basic salary (for HRA)", type: "number" }, { name: "hra_received", label: "HRA received", type: "number" }, { name: "rent_paid", label: "Rent paid in the year", type: "number" }] },
  INTEREST: { title: "Interest income", fields: [{ name: "payer_name", label: "Bank / payer", required: true }, { name: "kind", label: "Type", type: "select", options: ["SAVINGS", "FIXED_DEPOSIT", "RECURRING_DEPOSIT", "BONDS", "INCOME_TAX_REFUND", "OTHER"] }, { name: "amount", label: "Interest", type: "number", required: true }, { name: "tds", label: "TDS", type: "number" }, { name: "account_ref", label: "Account (last 4 digits)" }] },
  DIVIDEND: { title: "Dividend", fields: [{ name: "payer_name", label: "Company / fund", required: true }, { name: "amount", label: "Gross dividend", type: "number", required: true }, { name: "tds", label: "TDS", type: "number" }, { name: "paid_on", label: "Paid on", type: "date" }] },
  RENTAL: { title: "House property", fields: [{ name: "property_name", label: "Property", required: true }, { name: "is_self_occupied", label: "Self-occupied?", type: "select", options: ["false", "true"] }, { name: "annual_rent", label: "Annual rent received", type: "number" }, { name: "municipal_taxes", label: "Municipal taxes paid", type: "number" }, { name: "interest_on_loan", label: "Interest on housing loan", type: "number" }, { name: "tenant_name", label: "Tenant" }, { name: "tenant_tds", label: "TDS by tenant", type: "number" }] },
  CAPITAL_GAINS: { title: "Capital-gains transaction", fields: [{ name: "description", label: "Asset", required: true }, { name: "asset_class", label: "Asset class", type: "select", options: ["LISTED_EQUITY", "EQUITY_MF", "DEBT_MF", "LISTED_BOND", "UNLISTED_SHARES", "IMMOVABLE_PROPERTY", "GOLD", "OTHER"] }, { name: "acquisition_date", label: "Acquired on", type: "date" }, { name: "transfer_date", label: "Sold on", type: "date", required: true }, { name: "sale_consideration", label: "Sale consideration", type: "number", required: true }, { name: "cost_of_acquisition", label: "Cost of acquisition", type: "number", required: true }, { name: "transfer_expenses", label: "Transfer expenses", type: "number" }] },
  BUSINESS: { title: "Business / profession", fields: [{ name: "description", label: "Description", required: true }, { name: "nature", label: "Taxation", type: "select", options: ["PROFESSION_44ADA", "BUSINESS_44AD", "REGULAR"] }, { name: "amount", label: "Gross receipts / turnover", type: "number", required: true }, { name: "expenses", label: "Expenses (regular only)", type: "number" }, { name: "tds", label: "TDS", type: "number" }] },
  OTHER: { title: "Other income", fields: [{ name: "description", label: "Description", required: true }, { name: "category", label: "Category", type: "select", options: ["FAMILY_PENSION", "COMMISSION", "GIFT", "LOTTERY", "OTHER"] }, { name: "amount", label: "Amount", type: "number", required: true }, { name: "tds", label: "TDS", type: "number" }] },
  DEDUCTION: { title: "Deduction claim", fields: [{ name: "section", label: "Section", type: "select", options: ["80C", "80CCC", "80CCD1", "80CCD1B", "80CCD2", "80D", "80DD", "80DDB", "80E", "80EE", "80EEA", "80EEB", "80G", "80GG", "80GGA", "80GGC", "80TTA", "80TTB", "80U"], required: true }, { name: "description", label: "Description", required: true }, { name: "amount", label: "Amount", type: "number", required: true }] },
  INVESTMENT: { title: "Investment", fields: [{ name: "instrument", label: "Instrument", type: "select", options: ["PPF", "ELSS", "LIFE_INSURANCE", "EPF", "NSC", "TAX_SAVER_FD", "NPS", "SSY", "HOME_LOAN_PRINCIPAL", "TUITION_FEES", "HEALTH_INSURANCE", "OTHER"], required: true }, { name: "provider", label: "Provider" }, { name: "amount", label: "Amount", type: "number", required: true }, { name: "invested_on", label: "Date", type: "date" }] },
  LOAN: { title: "Loan", fields: [{ name: "kind", label: "Type", type: "select", options: ["HOME", "EDUCATION", "ELECTRIC_VEHICLE"] }, { name: "lender", label: "Lender", required: true }, { name: "interest_paid", label: "Interest paid", type: "number", required: true }, { name: "principal_repaid", label: "Principal repaid", type: "number" }] },
  TAX_PAYMENT: { title: "Advance / self-assessment tax", fields: [{ name: "kind", label: "Type", type: "select", options: ["ADVANCE_TAX", "SELF_ASSESSMENT_TAX"] }, { name: "amount", label: "Amount", type: "number", required: true }, { name: "paid_on", label: "Paid on", type: "date", required: true }, { name: "challan_ref", label: "Challan no." }] },
  BANK_ACCOUNT: { title: "Bank account", fields: [{ name: "bank_name", label: "Bank", required: true }, { name: "account_number", label: "Account number", required: true }, { name: "ifsc", label: "IFSC" }, { name: "account_type", label: "Type", type: "select", options: ["SAVINGS", "CURRENT", "NRO", "NRE"] }, { name: "is_primary_for_refund", label: "Primary for refund?", type: "select", options: ["true", "false"] }] },
};

function EntityForm({ head, initial, onSubmit, busy }: { head: string; initial?: Record<string, any>; onSubmit: (v: Record<string, any>) => void; busy: boolean }) {
  const def = FORMS[head];
  const [v, setV] = useState<Record<string, any>>(initial || {});
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); const out: Record<string, any> = {}; for (const f of def.fields) { const val = v[f.name]; if (val === undefined || val === "") continue; out[f.name] = f.type === "number" ? Number(val) : val === "true" ? true : val === "false" ? false : val; } onSubmit(out); }}>
      {def.fields.map((f) => (
        <Field key={f.name} label={f.label + (f.required ? " *" : "")}>
          {f.type === "select" ? <Select required={f.required} value={v[f.name] ?? f.options?.[0]} onChange={(e) => setV({ ...v, [f.name]: e.target.value })}>{f.options!.map((o) => <option key={o} value={o}>{titleCase(o)}</option>)}</Select>
            : <Input required={f.required} type={f.type === "number" ? "number" : f.type === "date" ? "date" : "text"} step={f.type === "number" ? "0.01" : undefined} value={v[f.name] ?? ""} onChange={(e) => setV({ ...v, [f.name]: e.target.value })} />}
        </Field>
      ))}
      <Button type="submit" loading={busy} className="w-full">Save</Button>
    </form>
  );
}

function Section({ title, subtitle, rows, columns, caseId, onChanged, head }: { title: string; subtitle?: string; rows: any[]; columns: { label: string; render: (r: any) => React.ReactNode; align?: "right" }[]; caseId: string; onChanged: () => void; head: string }) {
  const [add, setAdd] = useState(false);
  const [edit, setEdit] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<any>) => { setBusy(true); setErr(null); try { await fn(); onChanged(); setAdd(false); setEdit(null); } catch (e: any) { setErr(e.message); } finally { setBusy(false); } };
  return (
    <Card title={title} subtitle={subtitle} action={<Button size="sm" variant="secondary" onClick={() => setAdd(true)}><Plus size={14} /> Add</Button>}>
      {rows.length === 0 ? <p className="text-sm text-muted">Nothing recorded yet.</p> : (
        <Table head={<>{columns.map((c) => <th key={c.label} className={`${th} ${c.align === "right" ? "text-right" : ""}`}>{c.label}</th>)}<th className={th}>Status</th><th className={th}>Sources</th><th className={`${th} text-right`}>Actions</th></>}>
          {rows.map((r) => {
            const tv = r.amount || r.gross_salary || r.annual_rent_received || r.sale_consideration || r.gross_receipts || r.interest_paid || r.tax_deducted;
            return (
              <tr key={r.id}>
                {columns.map((c) => <td key={c.label} className={`${td} ${c.align === "right" ? "text-right tabular" : ""}`}>{c.render(r)}</td>)}
                <td className={td}><StatusBadge status={r.status} /></td>
                <td className={td}>{tv?.provenance ? <EvidenceList sources={tv.provenance.map((p: any) => ({ label: `${titleCase(p.source_type)}${p.reference ? " · " + p.reference : ""}`, document_id: p.document_id, confidence: p.confidence }))} /> : <span className="text-xs text-muted">—</span>}</td>
                <td className={`${td} text-right`}>
                  <div className="flex justify-end gap-1">
                    <EntityEvidenceButton caseId={caseId} entityId={r.id} label="Evidence" />
                    {r.status !== "USER_CONFIRMED" && <Button size="sm" variant="ghost" title="Confirm this value" onClick={() => act(() => Api.confirmEntity(caseId, r.id))}><Check size={14} /> Confirm</Button>}
                    {FORMS[head] && <Button size="sm" variant="ghost" onClick={() => setEdit(r)}><Pencil size={14} /></Button>}
                    <Button size="sm" variant="ghost" onClick={() => { if (confirm("Remove this entry?")) act(() => Api.deleteEntity(caseId, r.id)); }}><Trash2 size={14} /></Button>
                  </div>
                </td>
              </tr>
            );
          })}
        </Table>
      )}
      {err && <div className="mt-3"><Alert tone="danger">{err}</Alert></div>}
      <Drawer open={add} onClose={() => setAdd(false)} title={`Add · ${FORMS[head]?.title || title}`}>{FORMS[head] && <EntityForm head={head} busy={busy} onSubmit={(v) => act(() => Api.addEntity(caseId, head, v, true))} />}</Drawer>
      <Drawer open={!!edit} onClose={() => setEdit(null)} title={`Edit · ${FORMS[head]?.title || title}`}>{edit && FORMS[head] && <EntityForm head={head} busy={busy} initial={flatten(edit)} onSubmit={(v) => act(() => Api.patchEntity(caseId, edit.id, remap(head, v)))} />}</Drawer>
    </Card>
  );
}

function flatten(e: any): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, v] of Object.entries(e)) {
    if (v && typeof v === "object" && "amount" in (v as any)) out[k] = (v as any).amount;
    else if (typeof v !== "object") out[k] = v;
  }
  // API payload names differ slightly from model names for a few heads
  if (e.annual_rent_received) out.annual_rent = e.annual_rent_received.amount;
  if (e.municipal_taxes_paid) out.municipal_taxes = e.municipal_taxes_paid.amount;
  if (e.interest_on_borrowed_capital) out.interest_on_loan = e.interest_on_borrowed_capital.amount;
  if (e.gross_receipts) out.amount = e.gross_receipts.amount;
  return out;
}
function remap(head: string, v: Record<string, any>): Record<string, any> {
  const m: Record<string, string> = head === "RENTAL" ? { annual_rent: "annual_rent_received", municipal_taxes: "municipal_taxes_paid", interest_on_loan: "interest_on_borrowed_capital" } : head === "BUSINESS" ? { amount: "gross_receipts" } : {};
  const out: Record<string, any> = {};
  for (const [k, val] of Object.entries(v)) out[m[k] || k] = val;
  return out;
}

export default function ProfilePage() {
  const { activeCaseId, touch } = useApp();
  const { data: c, loading, reload } = useCaseData((id) => Api.case(id));
  const { data: reqs } = useCaseData((id) => Api.requirements(id));
  if (!activeCaseId) return <Alert tone="info">Start a return from the dashboard first.</Alert>;
  if (loading || !c) return <Skeleton className="h-96" />;
  const tp = c.taxpayer;
  const changed = () => { touch(); reload(); };
  const cid = activeCaseId;
  const amt = (k: string) => (r: any) => inr(r[k]?.amount);
  return (
    <div>
      <PageHeader title="My Tax Profile" subtitle="Everything extracted, entered, calculated or confirmed – in one place, with provenance." actions={<Link href="/onboarding"><Button variant="secondary">Edit essentials</Button></Link>} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Personal details" className="lg:col-span-2">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-3">
            {[["Name", tp.name], ["PAN", tp.pan_masked || "—"], ["Date of birth", dateShort(tp.date_of_birth)], ["Residential status", titleCase(tp.residential_status)], ["Taxpayer type", titleCase(tp.taxpayer_type)], ["Employment", titleCase(tp.employment_status)], ["Assessment year", tp.assessment_year], ["Employers", tp.employer_count ?? "—"], ["City", tp.city_type ? titleCase(tp.city_type) : "—"], ["Regime preference", titleCase(tp.regime_preference)]].map(([k, v]) => (
              <div key={k as string}><dt className="text-[11px] uppercase tracking-wide text-muted">{k}</dt><dd className="mt-0.5 font-medium">{(v as any) ?? "—"}</dd></div>
            ))}
          </dl>
          <div className="mt-3 flex flex-wrap gap-1.5">{(tp.income_sources || []).map((s: string) => <Badge key={s} tone="brand">{titleCase(s)}</Badge>)}<StatusBadge status={tp.field_status?.name || "USER_ENTERED"} /></div>
        </Card>
        <Card title="Still needed" subtitle="Astra's requirement engine">
          <ul className="space-y-1.5 text-sm">
            {(reqs?.items || []).filter((r: any) => r.status === "MISSING").slice(0, 8).map((r: any) => <li key={r.code} className="flex items-start gap-2"><span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${r.priority === "REQUIRED" ? "bg-danger" : r.priority === "RECOMMENDED" ? "bg-warning" : "bg-line"}`} /><span>{r.label}<span className="block text-xs text-muted">{r.why}</span></span></li>)}
            {reqs && !reqs.items.some((r: any) => r.status === "MISSING") && <li className="text-positive">Everything required is available.</li>}
          </ul>
        </Card>
      </div>
      <div className="mt-4 space-y-4">
        <Section head="SALARY" title="Salary" subtitle="Per employer, as per Form 16 / your entries" caseId={cid} onChanged={changed} rows={c.income.salary} columns={[{ label: "Employer", render: (r) => <span>{r.employer_name}<span className="block text-xs text-muted">{r.employer_tan || "TAN unknown"}{r.period_from ? ` · ${dateShort(r.period_from)} → ${dateShort(r.period_to)}` : ""}</span></span> }, { label: "Gross salary", render: amt("gross_salary"), align: "right" }, { label: "Exempt u/s 10", render: amt("exempt_allowances"), align: "right" }, { label: "TDS", render: amt("tds"), align: "right" }]} />
        <Section head="INTEREST" title="Interest income" caseId={cid} onChanged={changed} rows={c.income.interest} columns={[{ label: "Payer", render: (r) => <span>{r.payer_name}<span className="block text-xs text-muted">{titleCase(r.kind)}{r.account_ref ? ` · ${r.account_ref}` : ""}</span></span> }, { label: "Interest", render: amt("amount"), align: "right" }, { label: "TDS", render: amt("tds"), align: "right" }]} />
        <Section head="DIVIDEND" title="Dividends" caseId={cid} onChanged={changed} rows={c.income.dividend} columns={[{ label: "Company", render: (r) => <span>{r.payer_name}<span className="block text-xs text-muted">{dateShort(r.paid_on)}</span></span> }, { label: "Gross", render: amt("amount"), align: "right" }, { label: "TDS", render: amt("tds"), align: "right" }]} />
        <Section head="RENTAL" title="House property" caseId={cid} onChanged={changed} rows={c.income.rental} columns={[{ label: "Property", render: (r) => <span>{r.property_name}<span className="block text-xs text-muted">{r.is_self_occupied ? "Self-occupied" : `Let-out${r.tenant_name ? " to " + r.tenant_name : ""}`}</span></span> }, { label: "Annual rent", render: amt("annual_rent_received"), align: "right" }, { label: "Municipal tax", render: amt("municipal_taxes_paid"), align: "right" }, { label: "Loan interest", render: amt("interest_on_borrowed_capital"), align: "right" }]} />
        <Section head="CAPITAL_GAINS" title="Capital gains" subtitle="Classification (short/long, 111A/112A) is done by the engine per transaction" caseId={cid} onChanged={changed} rows={c.income.capital_gains} columns={[{ label: "Asset", render: (r) => <span>{r.description}<span className="block text-xs text-muted">{titleCase(r.asset_class)} · {dateShort(r.acquisition_date)} → {dateShort(r.transfer_date)}</span></span> }, { label: "Sale", render: amt("sale_consideration"), align: "right" }, { label: "Cost", render: amt("cost_of_acquisition"), align: "right" }, { label: "Expenses", render: amt("transfer_expenses"), align: "right" }]} />
        <Section head="BUSINESS" title="Business / profession" caseId={cid} onChanged={changed} rows={c.income.business} columns={[{ label: "Description", render: (r) => <span>{r.description}<span className="block text-xs text-muted">{titleCase(r.nature)}</span></span> }, { label: "Gross receipts", render: amt("gross_receipts"), align: "right" }, { label: "Expenses", render: amt("expenses"), align: "right" }]} />
        <Section head="OTHER" title="Other sources" caseId={cid} onChanged={changed} rows={c.income.other_sources} columns={[{ label: "Description", render: (r) => <span>{r.description}<span className="block text-xs text-muted">{titleCase(r.category)}</span></span> }, { label: "Amount", render: amt("amount"), align: "right" }]} />
        <div className="grid gap-4 lg:grid-cols-2">
          <Section head="DEDUCTION" title="Deductions claimed" caseId={cid} onChanged={changed} rows={c.deductions} columns={[{ label: "Section", render: (r) => <span>{r.section}<span className="block text-xs text-muted">{r.description}</span></span> }, { label: "Amount", render: amt("amount"), align: "right" }]} />
          <Section head="INVESTMENT" title="Investments" caseId={cid} onChanged={changed} rows={c.investments} columns={[{ label: "Instrument", render: (r) => <span>{titleCase(r.instrument)}<span className="block text-xs text-muted">{r.provider || ""}</span></span> }, { label: "Amount", render: amt("amount"), align: "right" }]} />
          <Section head="LOAN" title="Loans" caseId={cid} onChanged={changed} rows={c.loans} columns={[{ label: "Lender", render: (r) => <span>{r.lender}<span className="block text-xs text-muted">{titleCase(r.kind)}</span></span> }, { label: "Interest", render: amt("interest_paid"), align: "right" }, { label: "Principal", render: amt("principal_repaid"), align: "right" }]} />
          <Section head="TAX_PAYMENT" title="Advance / self-assessment tax" caseId={cid} onChanged={changed} rows={c.tax_payments} columns={[{ label: "Type", render: (r) => <span>{titleCase(r.kind)}<span className="block text-xs text-muted">{dateShort(r.paid_on)}{r.challan_ref ? ` · ${r.challan_ref}` : ""}</span></span> }, { label: "Amount", render: amt("amount"), align: "right" }]} />
        </div>
        <Section head="TDS" title="TDS / TCS ledger" subtitle="One row per source – Form 16, 26AS and AIS entries for the same deductor are reconciled, not summed" caseId={cid} onChanged={changed} rows={c.tax_deducted} columns={[{ label: "Deductor", render: (r) => <span>{r.deductor_name}<span className="block text-xs text-muted">{r.deductor_tan || ""} · s.{r.section} · {titleCase(r.source_type)}</span></span> }, { label: "Amount paid", render: amt("amount_paid_credited"), align: "right" }, { label: "Tax deducted", render: amt("tax_deducted"), align: "right" }]} />
        <Section head="BANK_ACCOUNT" title="Bank accounts" subtitle="Refund is credited to the primary account" caseId={cid} onChanged={changed} rows={c.bank_accounts} columns={[{ label: "Bank", render: (r) => <span>{r.bank_name}<span className="block text-xs text-muted">{r.account_number_masked} · {r.ifsc || ""}{r.is_primary_for_refund ? " · primary" : ""}</span></span> }, { label: "Type", render: (r) => titleCase(r.account_type) }]} />
      </div>
    </div>
  );
}
