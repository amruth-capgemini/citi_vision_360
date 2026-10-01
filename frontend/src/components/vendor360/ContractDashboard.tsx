import { useState } from "react";
import { RefreshCwIcon } from "lucide-react";
import type { Dashboard } from "../../api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";
import { Separator } from "@/components/ui/separator";
import { Spinner } from "@/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import { ATTENTION, day, riskText, signedPercent, slaText, usd } from "./format";

type Scope = "attention" | "all";

const PAGE_SIZE = 10;

/** The portfolio scan: renewal decisions, spend against budget, risk and SLA across every contract. */
export function ContractDashboard({ dashboard, scanning, onRescan }: { dashboard: Dashboard; scanning: boolean; onRescan: () => void }) {
  const [scope, setScope] = useState<Scope>("attention");
  const [page, setPage] = useState(1);
  const { totals } = dashboard;
  const filtered = scope === "all" ? dashboard.contracts : dashboard.contracts.filter((c) => c.attention !== "on_track");
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(page, pages); // a rescan can shrink the list
  const rows = filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);

  return (
    <Card>
      <CardHeader>
        <CardDescription className="text-xs font-semibold tracking-wide text-primary uppercase">Contract dashboard</CardDescription>
        <CardTitle className="text-2xl">Renewal decisions that need attention.</CardTitle>
        <CardAction className="flex divide-x">
          <Headline value={totals.past_notice} label="Past notice" className="text-destructive" />
          <Headline value={totals.decisions_due} label={`Due in ${dashboard.attention_days} days`} className="text-warning" />
          <Headline value={totals.in_review} label="In human review" />
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <Kpi label="2026 budget" value={usd(totals.budget_2026)} />
          <Kpi
            label="2026 forecast"
            value={usd(totals.forecast_2026)}
            note={totals.forecast_variance ? `${signedPercent(totals.forecast_variance.percent)} vs budget` : undefined}
          />
          <Kpi label="Actual YTD (Jan–Aug)" value={usd(totals.actual_ytd_2026)} />
          <Kpi label="High-risk contracts" value={totals.high_risk} note={`of ${totals.contracts}`} />
          <Kpi label="SLA breaches (Aug)" value={totals.sla_breaches} />
          <Kpi label="Missing assessments" value={totals.missing_assessment} />
        </div>
        <Separator />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Contracts</h3>
          <ToggleGroup
            variant="outline"
            size="sm"
            spacing={0}
            value={[scope]}
            onValueChange={(v) => {
              if (!v[0]) return;
              setScope(v[0] as Scope);
              setPage(1);
            }}
          >
            <ToggleGroupItem value="attention">Needs attention</ToggleGroupItem>
            <ToggleGroupItem value="all">All</ToggleGroupItem>
          </ToggleGroup>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Vendor and contract</TableHead>
              <TableHead>Renewal decision date</TableHead>
              <TableHead>Contract end</TableHead>
              <TableHead>Risk tier</TableHead>
              <TableHead>SLA</TableHead>
              <TableHead className="text-right">2026 forecast</TableHead>
              <TableHead>Attention state</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((c) => {
              const attention = ATTENTION[c.attention];
              return (
                <TableRow key={c.contract_id}>
                  <TableCell>
                    <div className="font-heading font-medium">{c.vendor_name ?? c.vendor_id}</div>
                    <div className="font-mono text-xs text-muted-foreground">
                      {c.contract_id} · {c.vendor_id}
                    </div>
                  </TableCell>
                  <TableCell className="tabular-nums">{day(c.renewal_decision_date)}</TableCell>
                  <TableCell className="tabular-nums">{day(c.end_date)}</TableCell>
                  <TableCell>{riskText(c)}</TableCell>
                  <TableCell className={cn(c.sla.breach && "text-destructive")}>{slaText(c)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    <div>{usd(c.forecast_2026)}</div>
                    <div className="text-xs text-muted-foreground">{signedPercent(c.forecast_variance?.percent)} vs budget</div>
                  </TableCell>
                  <TableCell>
                    <Badge variant={attention.variant}>{attention.label}</Badge>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
        {pages > 1 && (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground tabular-nums">
              {(current - 1) * PAGE_SIZE + 1}–{(current - 1) * PAGE_SIZE + rows.length} of {filtered.length} contracts
            </span>
            <Pages page={current} pages={pages} onPage={setPage} />
          </div>
        )}
      </CardContent>
      <CardFooter className="flex-wrap justify-between gap-2 text-xs text-muted-foreground">
        <span>
          Data as of {day(dashboard.as_of_date)} · scanned {new Date(dashboard.scanned_at).toLocaleString()} · rescans daily
        </span>
        <Button variant="outline" size="sm" onClick={onRescan} disabled={scanning}>
          {scanning ? <Spinner data-icon="inline-start" /> : <RefreshCwIcon data-icon="inline-start" />}
          {scanning ? "Scanning…" : "Rescan"}
        </Button>
      </CardFooter>
    </Card>
  );
}

/** < 1 2 … 9 > — the first, last and neighbouring pages, with gaps elided. */
function Pages({ page, pages, onPage }: { page: number; pages: number; onPage: (page: number) => void }) {
  const shown = [...new Set([1, page - 1, page, page + 1, pages])].filter((p) => p >= 1 && p <= pages).sort((a, b) => a - b);
  // The app routes by URL hash, so page links must not follow their href.
  const go = (p: number) => (e: React.MouseEvent) => {
    e.preventDefault();
    if (p >= 1 && p <= pages) onPage(p);
  };
  return (
    <Pagination className="mx-0 w-auto">
      <PaginationContent>
        <PaginationItem>
          <PaginationPrevious href="#" onClick={go(page - 1)} aria-disabled={page === 1} className={cn(page === 1 && "pointer-events-none opacity-50")} />
        </PaginationItem>
        {shown.map((p, i) => (
          <PaginationItem key={p} className="flex items-center">
            {i > 0 && p - shown[i - 1] > 1 && <PaginationEllipsis />}
            <PaginationLink href="#" isActive={p === page} onClick={go(p)}>
              {p}
            </PaginationLink>
          </PaginationItem>
        ))}
        <PaginationItem>
          <PaginationNext href="#" onClick={go(page + 1)} aria-disabled={page === pages} className={cn(page === pages && "pointer-events-none opacity-50")} />
        </PaginationItem>
      </PaginationContent>
    </Pagination>
  );
}

function Headline({ value, label, className }: { value: number; label: string; className?: string }) {
  return (
    <div className="flex flex-col px-4 first:pl-0 last:pr-0">
      <span className={cn("font-heading text-3xl font-semibold tabular-nums", className)}>{value}</span>
      <span className="text-xs whitespace-nowrap text-muted-foreground uppercase">{label}</span>
    </div>
  );
}

function Kpi({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return (
    <Card size="sm" className="bg-muted/40 ring-0">
      <CardHeader>
        <CardDescription className="text-xs">{label}</CardDescription>
        <CardTitle className="text-lg tabular-nums">{value}</CardTitle>
        {note && <CardDescription className="text-xs">{note}</CardDescription>}
      </CardHeader>
    </Card>
  );
}
