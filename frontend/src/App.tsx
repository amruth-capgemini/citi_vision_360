import { useEffect, useState } from "react";
import { AppSidebar, PAGES, type PageId } from "./components/AppSidebar";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { ScenariosPage } from "./pages/ScenariosPage";
import { Vendor360Page } from "./pages/Vendor360Page";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";

const PLACEHOLDER: Partial<Record<PageId, string>> = {
  portfolio: "A portfolio view of all 20 vendors: spend, renewals and risk at a glance.",
  review: "Draft recommendations waiting for an authorized human decision.",
  dependencies: "Which applications, services and roles depend on each vendor.",
};

/** The page in the URL hash (#/vendor360). A ?q= demo link opens Scenarios, which asks it. */
function pageFromUrl(): PageId {
  const id = window.location.hash.replace(/^#\/?/, "");
  if (PAGES.some((p) => p.id === id)) return id as PageId;
  return new URLSearchParams(window.location.search).has("q") ? "scenarios" : "vendor360";
}

export default function App() {
  const [page, setPage] = useState<PageId>(pageFromUrl);

  useEffect(() => {
    const onHash = () => setPage(pageFromUrl());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <SidebarProvider>
      <AppSidebar page={page} />
      <SidebarInset className="h-svh min-w-0 overflow-hidden">
        {/* The two working pages stay mounted so a running answer and the chat history survive navigation. */}
        <div hidden={page !== "vendor360"} className="h-full">
          <Vendor360Page />
        </div>
        <div hidden={page !== "scenarios"} className="h-full">
          <ScenariosPage />
        </div>
        {PAGES.filter((p) => PLACEHOLDER[p.id] && p.id === page).map((p) => (
          <PlaceholderPage key={p.id} title={p.label} icon={p.icon} description={PLACEHOLDER[p.id]!} />
        ))}
      </SidebarInset>
    </SidebarProvider>
  );
}
