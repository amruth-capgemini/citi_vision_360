import { useEffect, useState } from "react";
import type { CatalogSummary } from "../api";
import { getJson } from "../api";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyNote, Section } from "./common";

/** The metadata catalog: which systems and datasets the agents can read. */
export function SourcesPanel() {
  const [catalog, setCatalog] = useState<CatalogSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJson<CatalogSummary>("/api/sources").then(setCatalog, () => setError("The metadata catalog is unavailable."));
  }, []);

  if (error) return <EmptyNote>{error}</EmptyNote>;
  if (!catalog) {
    return (
      <div className="flex flex-col gap-3" aria-label="Loading catalog">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-2">
        {catalog.systems.map((s) => (
          <Card size="sm" key={s.system}>
            <CardHeader>
              <CardDescription>
                {s.system} datasets · {s.rows.toLocaleString()} rows
              </CardDescription>
              <CardTitle className="text-xl tabular-nums">{s.datasets}</CardTitle>
            </CardHeader>
          </Card>
        ))}
      </div>
      <Section title="Datasets" count={catalog.datasets.length}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Dataset</TableHead>
              <TableHead>Rows</TableHead>
              <TableHead>Fields</TableHead>
              <TableHead>Domains</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {catalog.datasets.map((d) => (
              <TableRow key={d.dataset}>
                <TableCell className="font-mono text-xs">{d.dataset}</TableCell>
                <TableCell className="tabular-nums">{d.rows ?? "—"}</TableCell>
                <TableCell className="tabular-nums">{d.fields}</TableCell>
                <TableCell>{d.domains.join(", ")}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>
    </div>
  );
}
