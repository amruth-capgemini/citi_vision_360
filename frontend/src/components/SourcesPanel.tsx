import { useEffect, useState } from "react";
import type { CatalogSummary } from "../api";
import { getJson } from "../api";
import { Empty, Section } from "./common";

/** The metadata catalog: which systems and datasets the agents can read. */
export function SourcesPanel() {
  const [catalog, setCatalog] = useState<CatalogSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJson<CatalogSummary>("/api/sources").then(setCatalog, () => setError("The metadata catalog is unavailable."));
  }, []);

  if (error) return <Empty>{error}</Empty>;
  if (!catalog) return <Empty>Loading catalog…</Empty>;
  return (
    <div>
      <div className="stats">
        {catalog.systems.map((s) => (
          <div className="stat" key={s.system}>
            <strong>{s.datasets}</strong>
            <span>
              {s.system} datasets · {s.rows.toLocaleString()} rows
            </span>
          </div>
        ))}
      </div>
      <Section title="Datasets" count={catalog.datasets.length}>
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Rows</th>
                <th>Fields</th>
                <th>Domains</th>
              </tr>
            </thead>
            <tbody>
              {catalog.datasets.map((d) => (
                <tr key={d.dataset}>
                  <td className="mono">{d.dataset}</td>
                  <td>{d.rows ?? "—"}</td>
                  <td>{d.fields}</td>
                  <td>{d.domains.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
