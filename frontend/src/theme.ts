/**
 * Theme preference.
 *
 * Three states, not two. "system" is the default and is genuinely different from
 * picking dark: a student whose phone flips to light at sunrise should follow it
 * unless they have said otherwise. `data-theme` is only written to <html> once
 * they choose, so first paint follows the OS with no flash and no script.
 *
 * localStorage rather than a backend field on purpose. Theme is per-device — a
 * sibling on the same phone shares the choice, which is correct, and a student
 * on a school computer should not drag their preference onto it. It is also the
 * one setting that must survive a 401, and session state does not.
 */

export type Theme = "system" | "light" | "dark";

const KEY = "guruji.theme";

function read(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    // Private mode, or storage disabled by policy. Following the OS is a fine
    // outcome — never let a preference read break the app.
    return "system";
  }
}

let current: Theme = read();
const listeners = new Set<() => void>();

function paint(t: Theme): void {
  const html = document.documentElement;
  if (t === "system") html.removeAttribute("data-theme");
  else html.setAttribute("data-theme", t);
}

export const theme = {
  get: (): Theme => current,

  /** What is actually on screen right now, resolving "system". */
  resolved: (): "light" | "dark" =>
    current !== "system"
      ? current
      : window.matchMedia?.("(prefers-color-scheme: light)").matches
        ? "light"
        : "dark",

  set(t: Theme): void {
    current = t;
    try {
      if (t === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, t);
    } catch {
      /* preference is still applied for this session */
    }
    // The `theming` class scopes a colour-only transition to the swap itself.
    // Left on permanently it would make every hover state feel laggy.
    const html = document.documentElement;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (!still) {
      html.classList.add("theming");
      window.setTimeout(() => html.classList.remove("theming"), 260);
    }
    paint(t);
    listeners.forEach((fn) => fn());
  },

  /** Tap target: flip whatever is currently showing. */
  toggle(): void {
    theme.set(theme.resolved() === "dark" ? "light" : "dark");
  },

  subscribe(fn: () => void): () => void {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
};

// Apply a stored choice before first paint. Nothing runs when the choice is
// "system", which is what keeps the default flash-free.
paint(current);
