import type { Attention, DashboardContract } from "../../api";

const COMPACT_USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });

/** "$53.1M" from an exact decimal string; "—" when absent. */
export function usd(value: string | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  return COMPACT_USD.format(Number(value));
}

/** "Apr 16, 2026" from an ISO date, without a timezone shift. */
export function day(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function signedPercent(percent: string | null | undefined) {
  if (percent === null || percent === undefined) return "—";
  const n = Number(percent);
  return `${n > 0 ? "+" : ""}${n.toFixed(1)}%`;
}

export const ATTENTION: Record<Attention, { label: string; variant: "destructive" | "warning" | "secondary" }> = {
  expired: { label: "Expired", variant: "destructive" },
  past_notice: { label: "Past notice", variant: "destructive" },
  notice_due: { label: "Decision due", variant: "warning" },
  expiring: { label: "Expiring", variant: "warning" },
  on_track: { label: "On track", variant: "secondary" },
};

export function slaText(c: DashboardContract) {
  const { breach, actual_percent: actual, target_percent: target, period } = c.sla;
  if (!period) return "No measurement";
  const values = actual && target ? ` · ${Number(actual).toFixed(2)}% / ${target}%` : "";
  return `${breach ? "Breach" : "Met"}${values}`;
}

export function riskText(c: DashboardContract) {
  if (c.risk_assessment_status === "Missing") return "No assessment";
  return c.risk_tier ?? "—";
}
