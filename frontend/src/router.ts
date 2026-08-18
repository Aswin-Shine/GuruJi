import { useEffect, useState } from "preact/hooks";

/**
 * Seven screens, no nested routes, no route params, no loaders. A router
 * library would be 8-15 kB gzipped to replace the thirty lines below, on a
 * product whose users are on budget phones and metered data.
 *
 * ponytail: if this ever grows nested layouts or per-route data loading,
 * replace it wholesale with preact-iso rather than growing it.
 */

export type Path =
  | "/"
  | "/verify"
  | "/start"
  | "/chat"
  | "/history"
  | "/account"
  | "/parent"
  /** The one parameterised route. Reopening a past conversation needs a real URL so
   *  Back works, the sidebar can mark the active row, and a reload does not silently
   *  land in a different session. Everything else stays a fixed path — this is not a
   *  general route matcher and should not become one. */
  | `/chat/${string}`;

const KNOWN: readonly Path[] = [
  "/",
  "/verify",
  "/start",
  "/chat",
  "/history",
  "/account",
  "/parent",
];

/** Extracts the conversation id from /chat/{id}, or null on any other route. */
export function conversationParam(pathname: string = location.pathname): string | null {
  const m = /^\/chat\/([A-Za-z0-9-]{6,64})$/.exec(pathname);
  return m?.[1] ?? null;
}

function normalise(pathname: string): Path {
  const hit = KNOWN.find((p) => p === pathname);
  if (hit) return hit;
  const id = conversationParam(pathname);
  return id ? (`/chat/${id}` as Path) : "/";
}

export function navigate(to: Path, replace = false): void {
  if (replace) history.replaceState(null, "", to);
  else history.pushState(null, "", to);
  dispatchEvent(new PopStateEvent("popstate"));
}

export function useRoute(): Path {
  const [path, setPath] = useState<Path>(() => normalise(location.pathname));
  useEffect(() => {
    const sync = (): void => setPath(normalise(location.pathname));
    addEventListener("popstate", sync);
    return () => removeEventListener("popstate", sync);
  }, []);
  return path;
}

/**
 * Reads the parent invite payload out of the URL fragment, captured once at
 * module evaluation — before Preact renders anything, and critically before
 * app.tsx's own "no session -> navigate to /" effect runs.
 *
 * That effect calls history.replaceState(null, "", "/") for any unauthenticated
 * visit to a route other than "/" or "/verify" — which silently destroys the
 * fragment before <Parent/> ever mounts to read it, since <Parent/> only mounts
 * AFTER sign-in succeeds. By then the fragment is long gone. A parent with no
 * existing session — the entire point of an invite link — hit this on every
 * single attempt: the link would open, bounce to sign-in, and the payload
 * would already be destroyed by the time authentication finished.
 *
 * Capturing here instead of inside Parent's effect sidesteps the problem
 * rather than trying to make every future navigate() call fragment-safe: the
 * payload lives in a module-level variable from the moment the page loads,
 * independent of whatever the URL does to itself afterward — including
 * Auth.tsx's own post-login redirect, which would have wiped it too.
 *
 * The fragment (not the query string) carries it because fragments are never
 * sent to a server: the PIN stays out of nginx access logs, out of any upstream
 * proxy's logs, and out of Referer headers. It is erased from the address bar
 * immediately below, at capture time, so it does not persist in browser
 * history or get re-shared if the parent forwards the page they're looking at.
 */
let pendingInvite: { studentId: string; pin: string } | null = readInviteFromLocation();

function readInviteFromLocation(): { studentId: string; pin: string } | null {
  const raw = location.hash.slice(1);
  if (!raw) return null;
  const q = new URLSearchParams(raw);
  const studentId = q.get("s");
  const pin = q.get("pin");
  history.replaceState(null, "", location.pathname);
  if (!studentId || !pin) return null;
  // Shape check before anything is sent: the id is a UUID and the PIN is six
  // digits. A malformed link is a typo, not a request worth making.
  if (!/^[0-9a-f-]{36}$/i.test(studentId) || !/^\d{6}$/.test(pin)) return null;
  return { studentId, pin };
}

/** One-shot: returns the invite captured at page load, then clears it so a
 *  second call — e.g. a double-invoked effect — can't replay it. */
export function takeInvite(): { studentId: string; pin: string } | null {
  const invite = pendingInvite;
  pendingInvite = null;
  return invite;
}
