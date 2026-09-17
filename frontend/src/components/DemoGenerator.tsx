"use client";
import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Wand2 } from "lucide-react";
import { Api } from "@/lib/api";
import { useApp } from "@/lib/store";
import { Alert, Button, Drawer, Field, Input, Select } from "@/components/ui";

export function DemoGenerator({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { refreshCases, setActiveCase, touch, supportedYears, currentYear } = useApp();
  const router = useRouter();
  const [scenarios, setScenarios] = useState<any[]>([]);
  const [scenario, setScenario] = useState("missing_income");
  const [seed, setSeed] = useState(42);
  const [ay, setAy] = useState(currentYear);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (open) Api.demoScenarios().then((r) => setScenarios(r.scenarios)).catch(() => setScenarios([]));
  }, [open]);
  useEffect(() => setAy(currentYear), [currentYear]);
  const generate = async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await Api.generateDemo(scenario, seed, ay);
      await refreshCases();
      setActiveCase(res.case.id);
      touch();
      onClose();
      router.push("/");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer open={open} onClose={onClose} title="Generate a demo taxpayer">
      <p className="mb-4 text-sm text-muted">Synthetic profiles with realistic relationships (salary → Form 16 → TDS → 26AS; bank → interest → AIS …) and hidden test scenarios the reconciliation engine must find. No real taxpayer data is used.</p>
      <div className="space-y-2">
        {scenarios.map((s) => (
          <label key={s.code} className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 ${scenario === s.code ? "border-brand bg-brand-soft/50" : "border-line hover:bg-surface-2"}`}>
            <input type="radio" name="scenario" className="mt-1" checked={scenario === s.code} onChange={() => setScenario(s.code)} />
            <span><span className="block text-sm font-medium">{s.label}</span><span className="block text-xs text-muted">{s.description}</span></span>
          </label>
        ))}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <Field label="Assessment year"><Select value={ay} onChange={(e) => setAy(e.target.value)}>{supportedYears.map((y) => <option key={y}>{y}</option>)}</Select></Field>
        <Field label="Seed" hint="Same seed → same taxpayer"><Input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></Field>
      </div>
      {err && <Alert tone="danger">{err}</Alert>}
      <Button className="mt-4 w-full" onClick={generate} loading={busy}><Wand2 size={16} /> Generate demo taxpayer</Button>
    </Drawer>
  );
}

