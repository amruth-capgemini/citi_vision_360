export function newSessionId() {
  return (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
}

/** The chat session for this tab (kept across reloads, not across tabs). */
export function storedSession() {
  try {
    const existing = sessionStorage.getItem("citi-session");
    if (existing) return existing;
    const id = newSessionId();
    sessionStorage.setItem("citi-session", id);
    return id;
  } catch {
    return newSessionId();
  }
}

export function storeSession(id: string) {
  try {
    sessionStorage.setItem("citi-session", id);
  } catch {
    /* per-tab convenience only */
  }
}
