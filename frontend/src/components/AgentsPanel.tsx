import { CircleCheckIcon, CircleXIcon } from "lucide-react";
import type { TraceEntry } from "../api";
import type { TimelineItem } from "../timeline";
import { detail, label } from "../timeline";
import { Badge } from "@/components/ui/badge";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Progress } from "@/components/ui/progress";
import { Spinner } from "@/components/ui/spinner";
import { AgentBadge, EmptyNote, RUN_STATUS, ms } from "./common";

/** Live timeline: supervisor -> specialists -> tools -> explorers -> sources, with durations. */
export function AgentsPanel({ items, running }: { items: TimelineItem[]; running: boolean }) {
  if (items.length === 0) return <EmptyNote>{running ? "Starting…" : "Ask a question to see the agents at work."}</EmptyNote>;
  const longest = Math.max(1, ...items.map((i) => i.duration_ms ?? 0));
  return (
    <ItemGroup className="gap-2">
      {items.map((item) => (
        <Item key={item.key} variant="outline" size="sm">
          <ItemMedia variant="icon">
            {item.live ? <Spinner /> : item.status === "stop" ? <CircleXIcon className="text-destructive" /> : <CircleCheckIcon className="text-success" />}
          </ItemMedia>
          <ItemContent>
            <ItemTitle>
              {label(item)}
              <AgentBadge agent={item.agent} />
              {item.status === "stop" && <Badge variant="destructive">stop</Badge>}
            </ItemTitle>
            <ItemDescription className="line-clamp-none text-xs">{detail(item)}</ItemDescription>
            {!item.live && item.duration_ms !== undefined && (
              <Progress value={Math.max(2, (100 * item.duration_ms) / longest)} aria-label={`${label(item)} duration`} />
            )}
          </ItemContent>
          <ItemActions className="self-start text-xs text-muted-foreground tabular-nums">
            {item.live ? <span className="shimmer">running…</span> : ms(item.duration_ms)}
          </ItemActions>
          {item.steps.length > 0 && (
            <ItemGroup className="basis-full gap-1.5 pl-6.5">
              {item.steps.map((s, i) => (
                <Step key={i} s={s} />
              ))}
            </ItemGroup>
          )}
        </Item>
      ))}
      {running && (
        <Item variant="muted" size="sm">
          <ItemMedia variant="icon">
            <Spinner />
          </ItemMedia>
          <ItemContent>
            <ItemTitle className="shimmer">working…</ItemTitle>
          </ItemContent>
        </Item>
      )}
    </ItemGroup>
  );
}

function Step({ s }: { s: TraceEntry }) {
  const action = String(s.action ?? s.node);
  const where = (s.datasets as string[] | undefined)?.join(", ") || (s.graph ? `${s.graph} graph` : "");
  return (
    <Item variant="muted" size="xs">
      <ItemContent>
        <ItemTitle className="flex-wrap">
          <code className="font-mono text-xs">
            {s.query_id ? `${s.query_id} ` : ""}
            {action}
          </code>
          {s.purpose ? <span className="text-xs font-normal text-muted-foreground">{String(s.purpose).replace("_", " ")}</span> : null}
          <Badge variant={RUN_STATUS[String(s.status)] ?? "secondary"}>{String(s.status)}</Badge>
          {s.row_count !== undefined && <span className="text-xs font-normal text-muted-foreground">{String(s.row_count)} rows</span>}
        </ItemTitle>
        {where && <ItemDescription className="font-mono">{where}</ItemDescription>}
        {s.thought ? <ItemDescription className="line-clamp-none italic">“{String(s.thought)}”</ItemDescription> : null}
        {s.message ? <ItemDescription className="line-clamp-none text-destructive">{String(s.message)}</ItemDescription> : null}
      </ItemContent>
      <ItemActions className="self-start text-xs text-muted-foreground tabular-nums">{ms(s.duration_ms as number | undefined)}</ItemActions>
    </Item>
  );
}
