"use client";
import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck, Sparkles } from "lucide-react";
import { Api, ApiError, STATIC_MODE } from "@/lib/api";
import { useApp } from "@/lib/store";
import { Alert, Button, Field, Input } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const { user, loading, refreshUser } = useApp();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [health, setHealth] = useState<any>(null);

  useEffect(() => {
    Api.health().then(setHealth).catch(() => setHealth({ demo_mode: false }));
  }, []);
  useEffect(() => {
    if (!loading && user) router.replace("/");
  }, [loading, user, router]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      await refreshUser();
      router.replace("/");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg px-4 py-10">
      <div className="grid w-full max-w-4xl gap-6 md:grid-cols-2">
        <div className="hidden flex-col justify-between rounded-3xl bg-brand p-8 text-brand-fg md:flex">
          <div>
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-white/20 text-lg font-bold">A</div>
            <h1 className="mt-6 text-3xl font-semibold leading-tight">Let&apos;s prepare your tax return</h1>
            <p className="mt-3 text-sm opacity-90">A professional filing workspace with an AI analyst inside it. Every number has a source, every calculation has an explanation, every conclusion is grounded in your documents.</p>
          </div>
          <ul className="space-y-2 text-sm opacity-90">
            <li>collect → extract → reconcile → calculate</li>
            <li>detect issues → explain → prepare → verify</li>
            <li>Deterministic tax engine · versioned rules by assessment year</li>
          </ul>
        </div>
        <div className="card p-7">
          <div className="mb-5">
            <h2 className="text-lg font-semibold">Sign in to ASTRA Tax</h2>
            <p className="text-sm text-muted">Your data is encrypted at rest and never leaves your workspace.</p>
          </div>
          {health?.demo_mode && (
            <Button className="w-full" onClick={() => run(() => Api.demoLogin())} loading={busy}><Sparkles size={16} /> {STATIC_MODE ? "Open my workspace" : "Continue with a demo workspace"}</Button>
          )}
          {STATIC_MODE && <p className="mt-3 text-xs text-muted">This hosted build keeps everything in your browser: no account, no server, nothing uploaded anywhere. Clearing site data resets it.</p>}
          <div className="my-5 flex items-center gap-3 text-xs text-muted"><span className="h-px flex-1 bg-line" />or use an account<span className="h-px flex-1 bg-line" /></div>
          <div className="mb-4 flex rounded-lg border border-line p-1 text-sm">
            {(["login", "register"] as const).map((m) => <button key={m} onClick={() => setMode(m)} className={`flex-1 rounded-md py-1.5 ${mode === m ? "bg-brand-soft text-brand font-medium" : "text-muted"}`}>{m === "login" ? "Sign in" : "Create account"}</button>)}
          </div>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); run(() => (mode === "login" ? Api.login(email, password) : Api.register(email, password, name || undefined))); }}>
            {mode === "register" && <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Priya Raghavan" /></Field>}
            <Field label="Email"><Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email" /></Field>
            <Field label="Password" hint={mode === "register" ? "At least 10 characters, mixed case and a digit." : undefined}><Input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} /></Field>
            {err && <Alert tone="danger">{err}</Alert>}
            <Button type="submit" variant="secondary" className="w-full" loading={busy}>{mode === "login" ? "Sign in" : "Create account"}</Button>
          </form>
          <p className="mt-5 flex items-start gap-2 text-[11px] text-muted"><ShieldCheck size={14} className="mt-0.5 shrink-0" /> Sessions are HttpOnly cookies with idle expiry; documents and the tax model are encrypted at rest; all actions are audit-logged.</p>
        </div>
      </div>
    </div>
  );
}
