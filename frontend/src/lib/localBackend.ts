/** Static-mode transport: the same API contract served by the Python domain code running in a Web Worker
 *  (Pyodide). Used only when NEXT_PUBLIC_STATIC_MODE=1 (GitHub Pages build). Nothing leaves the browser. */
export type BootStatus = { stage: string; message: string; ready: boolean; error?: string };

type Pending = { resolve: (v: any) => void; reject: (e: Error) => void };

const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";
let worker: Worker | null = null;
let readyPromise: Promise<void> | null = null;
let nextId = 1;
const pending = new Map<number, Pending>();
let status: BootStatus = { stage: "idle", message: "Starting the in-browser tax engine…", ready: false };
const listeners = new Set<(s: BootStatus) => void>();

function setStatus(s: Partial<BootStatus>) {
  status = { ...status, ...s };
  listeners.forEach((l) => l(status));
}

export function onBootStatus(fn: (s: BootStatus) => void): () => void {
  listeners.add(fn);
  fn(status);
  return () => listeners.delete(fn);
}

export function isStaticMode(): boolean {
  return process.env.NEXT_PUBLIC_STATIC_MODE === "1";
}

export function ensureWorker(): Promise<void> {
  if (readyPromise) return readyPromise;
  readyPromise = new Promise<void>((resolve, reject) => {
    if (typeof window === "undefined") {
      reject(new Error("no window"));
      return;
    }
    worker = new Worker(`${BASE}/py/worker.js`);
    worker.onmessage = (ev: MessageEvent) => {
      const m = ev.data || {};
      if (m.type === "progress") setStatus({ stage: m.stage, message: m.message });
      else if (m.type === "ready") {
        setStatus({ stage: "ready", message: "Ready", ready: true });
        resolve();
      } else if (m.type === "error") {
        setStatus({ stage: "error", message: m.message, error: m.message });
        reject(new Error(m.message));
        pending.forEach((p) => p.reject(new Error(m.message)));
        pending.clear();
      } else if (m.type === "response") {
        const p = pending.get(m.id);
        if (p) {
          pending.delete(m.id);
          p.resolve(m);
        }
      }
    };
    worker.onerror = (e) => {
      const msg = e.message || "Worker failed to start";
      setStatus({ stage: "error", message: msg, error: msg });
      reject(new Error(msg));
    };
    worker.postMessage({ type: "init", base: `${window.location.origin}${BASE}` });
  });
  return readyPromise;
}

export async function localRequest(method: string, path: string, body?: unknown, file?: { name: string; type: string; bytes: ArrayBuffer }): Promise<{ status: number; body: any }> {
  await ensureWorker();
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    const msg: any = { type: "request", id, method, path, body: body === undefined ? null : body, file: file || null };
    worker!.postMessage(msg, file ? [file.bytes] : []);
  });
}
