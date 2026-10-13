export const DASH = "—";

export function label(value: string | null | undefined): string {
  if (!value) return DASH;
  return value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  const scaled = value * 100;
  const d = digits === 0 && scaled !== 0 && Math.abs(scaled) < 10 && !Number.isInteger(scaled) ? 1 : digits;
  return `${scaled.toFixed(d)}%`;
}

export function num(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  if (Math.abs(value) < 10_000) return num(value);
  return value.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 });
}

/** 420 -> "420 ms", 1430 -> "1.4 s", 74000 -> "1 min 14 s". */
export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} s`;
  const minutes = Math.floor(value / 60_000);
  const seconds = Math.round((value % 60_000) / 1000);
  if (minutes < 60) return seconds ? `${minutes} min ${seconds} s` : `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min`;
}

export function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return ms(value * 1000);
}

export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  if (value === 0) return "$0.00";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
}

export function money(value: number | null | undefined, currency?: string | null): string {
  if (value === null || value === undefined) return DASH;
  const amount = value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency ? `${amount} ${currency}` : amount;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function whenFull(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** "3 min ago"; `now` comes from useNow() so rendering stays pure. */
export function ago(iso: string | null | undefined, now: number): string {
  if (!iso) return DASH;
  if (!now) return when(iso);
  const diff = Math.max(0, now - new Date(iso).getTime());
  const s = Math.round(diff / 1000);
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} d ago`;
  return when(iso);
}

export function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return DASH;
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (Array.isArray(value)) return value.length ? value.map(display).join(", ") : DASH;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Replace attachment payloads (base64) before anything is rendered. */
export function stripBinary(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stripBinary);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, inner] of Object.entries(value as Record<string, unknown>)) {
      if (key === "content_base64" || key.endsWith("_base64")) {
        const size = typeof inner === "string" ? Math.round((inner.length * 3) / 4 / 1024) : 0;
        out[key] = `[binary omitted, about ${size} KB]`;
      } else {
        out[key] = stripBinary(inner);
      }
    }
    return out;
  }
  return value;
}

export function attachmentNames(input: unknown): string[] {
  if (!input || typeof input !== "object") return [];
  const obj = input as Record<string, unknown>;
  const names: string[] = [];
  if (Array.isArray(obj.attachments)) {
    for (const a of obj.attachments) {
      if (a && typeof a === "object" && typeof (a as Record<string, unknown>).filename === "string") names.push(String((a as Record<string, unknown>).filename));
    }
  }
  if (typeof obj.filename === "string" && obj.filename) names.push(obj.filename);
  return names;
}
