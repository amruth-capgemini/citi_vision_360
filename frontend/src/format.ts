// Display-only line breaks for SQL and Cypher. Only whitespace outside quotes changes; the
// Evidence tab can always show the query exactly as executed.

const SQL_BREAKS = ["UNION ALL", "UNION", "SELECT", "FROM", "LEFT JOIN", "INNER JOIN", "JOIN", "WHERE", "GROUP BY", "HAVING", "ORDER BY", "LIMIT"];
const CYPHER_BREAKS = ["OPTIONAL MATCH", "MATCH", "WHERE", "WITH", "UNWIND", "RETURN", "ORDER BY", "LIMIT"];

export function formatQuery(query: string, language: "sql" | "cypher"): string {
  const breaks = language === "sql" ? SQL_BREAKS : CYPHER_BREAKS;
  let out = "";
  let quote: string | null = null;
  let depth = 0;
  for (let i = 0; i < query.length; i++) {
    const ch = query[i];
    if (quote) {
      out += ch;
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") {
      quote = ch;
      out += ch;
      continue;
    }
    if (ch === "(") depth++;
    if (ch === ")") depth = Math.max(0, depth - 1);
    const boundary = i === 0 || /\s|\(|\)/.test(query[i - 1]);
    const keyword = boundary ? breaks.find((k) => matchesAt(query, i, k)) : undefined;
    if (keyword && out.trim()) {
      out = out.replace(/\s+$/, "") + "\n" + "  ".repeat(Math.min(depth, 4)) + query.slice(i, i + keyword.length);
      i += keyword.length - 1;
      continue;
    }
    out += ch;
  }
  return out;
}

function matchesAt(text: string, index: number, keyword: string) {
  const slice = text.slice(index, index + keyword.length);
  const after = text[index + keyword.length];
  return slice.toUpperCase() === keyword && (after === undefined || /\s|\(/.test(after));
}
