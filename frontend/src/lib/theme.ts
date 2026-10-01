import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";

const KEY = "citi-theme";
const media = window.matchMedia("(prefers-color-scheme: dark)");
const listeners = new Set<() => void>();

/** The viewer's explicit choice, if they made one; otherwise the OS setting applies. */
function stored(): Theme | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

function current(): Theme {
  return stored() ?? (media.matches ? "dark" : "light");
}

function apply() {
  // shadcn themes switch on a .dark class on the root element.
  document.documentElement.classList.toggle("dark", current() === "dark");
  listeners.forEach((notify) => notify());
}

export function initTheme() {
  apply();
  media.addEventListener("change", () => {
    if (!stored()) apply();
  });
}

export function setTheme(theme: Theme) {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* the choice just won't persist */
  }
  apply();
}

export function useTheme(): Theme {
  return useSyncExternalStore((notify) => {
    listeners.add(notify);
    return () => listeners.delete(notify);
  }, current);
}
