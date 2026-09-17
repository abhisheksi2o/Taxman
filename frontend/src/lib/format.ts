/** Indian-format currency & small helpers shared by every page. */
export function inr(value: number | string | null | undefined, opts: { decimals?: number; signed?: boolean; compact?: boolean } = {}): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(n)) return "—";
  const decimals = opts.decimals ?? 0;
  const abs = Math.abs(n);
  if (opts.compact && abs >= 1e5) {
    const trim = (x: number) => (Number.isInteger(x) ? String(x) : x.toFixed(x < 10 ? 2 : 1).replace(/\.?0+$/, ""));
    if (abs >= 1e7) return `${n < 0 ? "-" : ""}₹${trim(abs / 1e7)} Cr`;
    return `${n < 0 ? "-" : ""}₹${trim(abs / 1e5)} L`;
  }
  const fixed = abs.toFixed(decimals);
  const [whole, frac] = fixed.split(".");
  let grouped = whole;
  if (whole.length > 3) {
    const head = whole.slice(0, -3);
    const tail = whole.slice(-3);
    grouped = head.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + tail;
  }
  const sign = n < 0 ? "-" : opts.signed && n > 0 ? "+" : "";
  return `${sign}₹${grouped}${frac ? "." + frac : ""}`;
}

export function pct(v: number | null | undefined, decimals = 0): string {
  if (v === null || v === undefined) return "—";
  return `${v.toFixed(decimals)}%`;
}

export function dateShort(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function timeShort(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return `${dateShort(iso)} · ${timeShort(iso)}`;
}

export const STATUS_META: Record<string, { label: string; tone: string }> = {
  EXTRACTED: { label: "Extracted", tone: "info" },
  CALCULATED: { label: "Calculated", tone: "brand" },
  USER_ENTERED: { label: "User-entered", tone: "neutral" },
  USER_CONFIRMED: { label: "User-confirmed", tone: "positive" },
  AI_SUGGESTED: { label: "AI-suggested", tone: "warning" },
  UNRESOLVED: { label: "Unresolved", tone: "danger" },
};

export const SEVERITY_META: Record<string, { label: string; tone: string; icon: string }> = {
  HIGH: { label: "High priority", tone: "danger", icon: "🔴" },
  MEDIUM: { label: "Review", tone: "warning", icon: "🟠" },
  LOW: { label: "Review", tone: "warning", icon: "🟡" },
  INFO: { label: "Information", tone: "info", icon: "🔵" },
};

export const KIND_LABEL: Record<string, string> = {
  MISSING: "Missing information",
  MISMATCH: "Mismatch",
  DUPLICATE: "Duplicate",
  CLASSIFICATION: "Classification",
  TIMING: "Timing",
};

export function titleCase(s: string | null | undefined): string {
  if (!s) return "";
  return s.toLowerCase().replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
