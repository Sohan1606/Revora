export function rupees(paise: number | null | undefined): string {
  if (paise == null) return "—";
  const v = paise / 100;
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(v);
}

export function pct(x: number | null | undefined, digits = 1): string {
  if (x == null) return "—";
  return `${(x * 100).toFixed(digits)}%`;
}

export function shortId(id: string | null | undefined): string {
  return id ? `${id.slice(0, 8)}…` : "—";
}

export function dt(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export const ROLE_LEVEL: Record<string, number> = {
  viewer: 1,
  operator: 2,
  admin: 3,
  owner: 4,
};

export function atLeast(role: string | undefined, min: string): boolean {
  return ROLE_LEVEL[role ?? "viewer"] >= ROLE_LEVEL[min];
}
