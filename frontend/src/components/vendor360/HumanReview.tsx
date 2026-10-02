import { useState } from "react";
import { DatabaseIcon, FileTextIcon } from "lucide-react";
import type { DashboardContract, ReviewItem } from "../../api";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Item, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { day, riskText, signedPercent, slaText, usd } from "./format";

export interface ReviewEntry {
  contract: DashboardContract;
  item: ReviewItem | null;
}

const SHOWN = 4;

/**
 * Draft decisions a human must validate. Without a question this is the portfolio queue (notice
 * deadlines, SLA breaches, high risk); after a question it is the contracts the answer is about,
 * with the agent's draft on top.
 */
export function HumanReview({ entries, draft, specialist }: { entries: ReviewEntry[]; draft?: string | null; specialist?: string }) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? entries : entries.slice(0, SHOWN);
  const drafts = entries.filter((e) => e.item).length;

  return (
    <Card>
      <CardHeader>
        <CardDescription className="text-2xl font-semibold tracking-wide text-primary uppercase">Human review</CardDescription>
        <CardTitle className="text-x">
          {draft ? "Agent draft for review." : `${drafts} draft decision${drafts === 1 ? "" : "s"} require${drafts === 1 ? "s" : ""} review.`}
        </CardTitle>
        <CardAction className="max-w-xs text-right text-xs text-muted-foreground">
          {draft
            ? "Drafted from this question's evidence. A human must validate it before any approval or rejection."
            : "Contracts with a passed or near notice deadline, an SLA breach, high risk or a missing assessment. A human must validate the evidence before any approval or rejection."}
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {draft && (
          <Alert>
            <FileTextIcon />
            <AlertTitle>Draft recommendation{specialist ? ` · ${specialist}` : ""}</AlertTitle>
            <AlertDescription className="text-foreground">{draft}</AlertDescription>
          </Alert>
        )}
        {draft && entries.length === 0 && (
          <p className="text-sm text-muted-foreground">The answer does not name a contract in the latest scan, so there is no contract snapshot to review.</p>
        )}
        {shown.map((entry) => (
          <ReviewCard key={entry.contract.contract_id} entry={entry} />
        ))}
        {entries.length > SHOWN && (
          <Button variant="outline" className="self-center" onClick={() => setShowAll(!showAll)}>
            {showAll ? "Show fewer" : `Show all ${entries.length}`}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function ReviewCard({ entry: { contract: c, item } }: { entry: ReviewEntry }) {
  return (
    <Card size="sm" className="bg-background">
      <CardHeader>
        <CardDescription className={cn("text-xs font-semibold tracking-wide uppercase", item?.priority === "high" ? "text-destructive" : "text-primary")}>
          {item ? `${item.priority} priority / draft` : "No review flags in the latest scan"}
        </CardDescription>
        <CardTitle className="font-heading text-lg">{c.vendor_name ?? c.vendor_id}</CardTitle>
        <CardDescription className="font-mono text-xs">
          {c.contract_id} · {c.vendor_id}
          {c.description ? ` · ${c.description}` : ""}
        </CardDescription>
        {item && <CardAction className="max-w-xs text-right font-heading text-sm">{item.headline}</CardAction>}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {item && (
          <div className="flex flex-wrap gap-1.5">
            {item.reasons.map((r) => (
              <Badge key={r} variant={item.priority === "high" ? "warning" : "secondary"} className="h-auto py-1 whitespace-normal">
                {r}
              </Badge>
            ))}
          </div>
        )}
        <Separator />
        <div className="grid gap-4 md:grid-cols-[2fr_1fr]">
          <section className="flex flex-col gap-2">
            <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Review snapshot</h4>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-lg bg-muted p-4">
              <Fact label="SLA status" value={slaText(c)} />
              <Fact label="Contract risk tier" value={riskText(c)} />
              <Fact label="VRM status" value={c.vrm_status ?? "—"} />
              <Fact label="Renewal decision date" value={day(c.renewal_decision_date)} />
              <Fact label="Risk assessment" value={[c.risk_assessment_status, c.risk_assessment_date && day(c.risk_assessment_date)].filter(Boolean).join(" · ") || "—"} />
              <Fact label="2026 forecast" value={`${usd(c.forecast_2026)} (${signedPercent(c.forecast_variance?.percent)} vs budget)`} />
              <Fact label="Contract end" value={day(c.end_date)} />
              <Fact label="Notice period" value={c.notice_days ? `${c.notice_days} days${c.automatic_renewal ? " · auto-renews" : ""}` : "—"} />
            </dl>
          </section>
          <section className="flex flex-col gap-2">
            <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Source data</h4>
            <ItemGroup className="gap-2">
              <Item variant="outline" size="xs">
                <ItemMedia variant="icon">
                  <DatabaseIcon />
                </ItemMedia>
                <ItemContent>
                  <ItemTitle className="font-mono text-xs">{c.source.dataset}</ItemTitle>
                  <ItemDescription className="font-mono text-xs">{c.source.record_id}</ItemDescription>
                </ItemContent>
              </Item>
              {c.source.renewal_terms && (
                <Item variant="outline" size="xs">
                  <ItemMedia variant="icon">
                    <FileTextIcon />
                  </ItemMedia>
                  <ItemContent>
                    <ItemTitle className="text-xs">Renewal clause</ItemTitle>
                    <ItemDescription className="font-mono text-xs">{c.contract_id} contract document</ItemDescription>
                  </ItemContent>
                </Item>
              )}
            </ItemGroup>
            {c.warning && <p className="text-xs text-muted-foreground">Source warning: {c.warning}</p>}
          </section>
        </div>
      </CardContent>
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground uppercase">{label}</dt>
      <dd className="text-sm font-medium">{value}</dd>
    </div>
  );
}
