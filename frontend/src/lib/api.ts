/** Thin fetch wrapper. Every call goes to the same-origin /api proxy with the session cookie and the
 *  CSRF client header. No secrets, keys or backend hosts live in the browser bundle. */
export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, message: string, data?: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

type Init = Omit<RequestInit, "body"> & { json?: unknown; form?: FormData; body?: BodyInit | null };

export async function api<T = any>(path: string, init: Init = {}): Promise<T> {
  const headers: Record<string, string> = { "x-astra-client": "web", ...((init.headers as Record<string, string>) || {}) };
  let body: BodyInit | null | undefined = init.body;
  if (init.json !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  if (init.form) body = init.form;
  const res = await fetch(`/api${path}`, { ...init, headers, body, credentials: "include", cache: "no-store" });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const detail = typeof data === "object" && data?.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText;
    throw new ApiError(res.status, detail, data);
  }
  return data as T;
}

export const Api = {
  health: () => api("/health"),
  me: () => api("/auth/me"),
  demoLogin: () => api("/auth/demo", { method: "POST" }),
  login: (email: string, password: string) => api("/auth/login", { method: "POST", json: { email, password } }),
  register: (email: string, password: string, display_name?: string) => api("/auth/register", { method: "POST", json: { email, password, display_name } }),
  logout: () => api("/auth/logout", { method: "POST" }),

  cases: () => api("/cases"),
  createCase: (assessment_year: string, label?: string) => api("/cases", { method: "POST", json: { assessment_year, label } }),
  case: (id: string) => api(`/cases/${id}`),
  deleteCase: (id: string) => api(`/cases/${id}`, { method: "DELETE" }),
  dashboard: (id: string) => api(`/cases/${id}/dashboard`),
  onboarding: (id: string) => api(`/cases/${id}/onboarding`),
  patchProfile: (id: string, values: Record<string, unknown>, confirmed = true) => api(`/cases/${id}/profile`, { method: "PATCH", json: { values, confirmed } }),
  requirements: (id: string) => api(`/cases/${id}/requirements`),
  addEntity: (id: string, head: string, payload: Record<string, unknown>, confirmed = true) => api(`/cases/${id}/entities`, { method: "POST", json: { head, payload, confirmed } }),
  patchEntity: (id: string, entityId: string, changes: Record<string, unknown>) => api(`/cases/${id}/entities/${entityId}`, { method: "PATCH", json: { changes } }),
  confirmEntity: (id: string, entityId: string) => api(`/cases/${id}/entities/${entityId}/confirm`, { method: "POST" }),
  deleteEntity: (id: string, entityId: string) => api(`/cases/${id}/entities/${entityId}`, { method: "DELETE" }),

  documents: (id: string) => api(`/cases/${id}/documents`),
  upload: (id: string, file: File, type?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (type) form.append("document_type", type);
    return api(`/cases/${id}/documents`, { method: "POST", form });
  },
  document: (id: string, docId: string) => api(`/cases/${id}/documents/${docId}`),
  reprocess: (id: string, docId: string, type?: string) => api(`/cases/${id}/documents/${docId}/reprocess${type ? `?document_type=${type}` : ""}`, { method: "POST" }),
  deleteDocument: (id: string, docId: string) => api(`/cases/${id}/documents/${docId}`, { method: "DELETE" }),

  reconciliation: (id: string) => api(`/cases/${id}/reconciliation`),
  runReconciliation: (id: string) => api(`/cases/${id}/reconciliation/run`, { method: "POST" }),
  item: (id: string, itemId: string) => api(`/cases/${id}/reconciliation/${itemId}`),
  resolve: (id: string, itemId: string, action: string, payload?: Record<string, unknown>, note?: string) =>
    api(`/cases/${id}/reconciliation/${itemId}/resolve`, { method: "POST", json: { action, payload, note } }),
  reopen: (id: string, itemId: string) => api(`/cases/${id}/reconciliation/${itemId}/reopen`, { method: "POST" }),
  issues: (id: string) => api(`/cases/${id}/issues`),

  computation: (id: string) => api(`/cases/${id}/computation`),
  explain: (id: string, regime: string, lineId: string) => api(`/cases/${id}/computation/explain/${regime}/${lineId}`),
  evidence: (id: string, entityId: string) => api(`/cases/${id}/evidence/${entityId}`),
  evidenceTree: (id: string, regime?: string) => api(`/cases/${id}/evidence/tree${regime ? `?regime=${regime}` : ""}`),
  rules: (ay: string) => api(`/rules/${ay}`),

  astra: (id: string) => api(`/cases/${id}/astra`),
  ask: (id: string, question: string, mode?: string) => api(`/cases/${id}/astra/ask`, { method: "POST", json: { question, mode } }),
  clearAstra: (id: string) => api(`/cases/${id}/astra`, { method: "DELETE" }),

  review: (id: string) => api(`/cases/${id}/review`),
  confirmReview: (id: string, declarations: Record<string, boolean>, selected_regime: string, acknowledge_open_issues: boolean) =>
    api(`/cases/${id}/review/confirm`, { method: "POST", json: { declarations, selected_regime, acknowledge_open_issues } }),
  resetReview: (id: string) => api(`/cases/${id}/review/reset`, { method: "POST" }),
  audit: (id: string) => api(`/cases/${id}/audit`),

  demoScenarios: () => api("/demo/scenarios"),
  generateDemo: (scenario: string, seed: number, assessment_year: string) => api("/demo/generate", { method: "POST", json: { scenario, seed, assessment_year } }),

  evalRun: (body: Record<string, unknown>) => api("/dev/evaluation/run", { method: "POST", json: body }),
  evalRuns: () => api("/dev/evaluation/runs"),
  evalCase: (id: string) => api(`/dev/evaluation/case/${id}`),
};
