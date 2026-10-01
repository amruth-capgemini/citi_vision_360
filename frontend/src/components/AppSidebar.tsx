import { useEffect, useState } from "react";
import type { LucideIcon } from "lucide-react";
import { BriefcaseBusinessIcon, Building2Icon, DiamondIcon, FlaskConicalIcon, NetworkIcon, UserCheckIcon } from "lucide-react";
import type { Health } from "../api";
import { getJson } from "../api";
import { Badge } from "@/components/ui/badge";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

export type PageId = "portfolio" | "vendor360" | "review" | "dependencies" | "scenarios";

export const PAGES: { id: PageId; label: string; icon: LucideIcon }[] = [
  { id: "portfolio", label: "Portfolio", icon: BriefcaseBusinessIcon },
  { id: "vendor360", label: "Vendor 360", icon: Building2Icon },
  { id: "review", label: "Human review", icon: UserCheckIcon },
  { id: "dependencies", label: "Dependencies", icon: NetworkIcon },
  { id: "scenarios", label: "Scenarios", icon: FlaskConicalIcon },
];

export function AppSidebar({ page }: { page: PageId }) {
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" render={<a href="#/vendor360" />} tooltip="Vendor Decision Intelligence">
              <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
                <DiamondIcon />
              </div>
              <div className="grid flex-1 text-left leading-tight">
                <span className="truncate font-heading font-semibold">Vendor Decision</span>
                <span className="truncate text-xs text-sidebar-foreground/70">Intelligence</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Workspace</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu className="gap-1">
              {PAGES.map(({ id, label, icon: Icon }) => (
                <SidebarMenuItem key={id}>
                  <SidebarMenuButton render={<a href={`#/${id}`} />} isActive={page === id} tooltip={label}>
                    <Icon />
                    <span>{label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <HealthStatus />
        <p className="px-2 text-xs text-sidebar-foreground/60 group-data-[collapsible=icon]:hidden"></p>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}

/** Which backends the API can reach (Postgres, Neo4j, the model). */
function HealthStatus() {
  const [health, setHealth] = useState<Health | null | undefined>(undefined);
  useEffect(() => {
    getJson<Health>("/api/health").then(setHealth, () => setHealth(null));
  }, []);
  if (health === undefined) return null;
  return (
    // The sidebar is dark in both themes, so the badges use the dark palette.
    <div className="dark flex flex-wrap gap-1 px-2 group-data-[collapsible=icon]:hidden">
      {health === null ? (
        <Badge variant="destructive">API offline?</Badge>
      ) : (
        Object.entries(health.checks).map(([name, check]) => (
          <Badge key={name} variant={check.ok ? "success" : "destructive"} title={`${name}: ${check.ok ? "ok" : "unavailable"}`}>
            {name}
          </Badge>
        ))
      )}
    </div>
  );
}
