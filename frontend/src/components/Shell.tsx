import type { ComponentChildren, JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import type { ConversationOut } from "../backend";
import { navigate, useRoute } from "../router";
import { session } from "../session";
import { theme } from "../theme";

/**
 * The app shell: a sidebar rail and a main pane.
 *
 * This is the fix for "every screen is a phone screen in a browser window". The
 * previous layout was one 30rem column centred at every viewport width, which on
 * a 1400px desktop produced the void in the screenshot — a composer pinned to the
 * bottom of a 900px viewport with a screen of nothing above it.
 *
 * One DOM, two layouts, no JS breakpoint listener:
 *   < 900px   the sidebar is an off-canvas drawer behind a hamburger; the main
 *             pane is the whole screen and the composer sits where a thumb is.
 *   >= 900px  the sidebar is permanent and the pane is a 48rem reading column.
 *
 * The pane stays a column at desktop rather than filling the window. Chat at
 * 1400px is unreadable line length — "responsive" means the layout suits the
 * device, not that content stretches to fill whatever space exists.
 */

function SunIcon(): JSX.Element {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2.6v2M12 19.4v2M2.6 12h2M19.4 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4" />
    </svg>
  );
}

function MoonIcon(): JSX.Element {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.4 8.4 0 1 0 10.2 10.2Z" />
    </svg>
  );
}

export function ThemeToggle(): JSX.Element {
  const [, tick] = useState(0);
  useEffect(() => theme.subscribe(() => tick((n) => n + 1)), []);
  const dark = theme.resolved() === "dark";
  return (
    <button
      class="icon-btn"
      onClick={() => theme.toggle()}
      // The label states the OUTCOME, not the current state. "Dark mode" on a
      // button is ambiguous — a screen reader user cannot tell whether it
      // describes what is on or what will happen.
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      title={dark ? "Light theme" : "Dark theme"}
    >
      {dark ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

/** The GuruJi mark: an open book with a spark above it.
 *
 *  Previously this slot held the Devanagari गु set in the UI font. A typeset
 *  glyph has a ceiling as a logotype — गु carries a headstroke, a bowl and a ु
 *  matra below, and at 22px those strokes are around one pixel each. On a 2x
 *  panel they vanish into the fill, which is exactly what "can't see the logo"
 *  meant. A drawn mark with a 2px stroke survives the same size, and survives
 *  the 16px favicon it will eventually need to be.
 *
 *  Open book = taught from a real textbook, which is the entire product claim.
 *  Two shapes, one stroke weight, no detail below ~2px. */
function MarkGlyph(): JSX.Element {
  return (
    <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M3.5 6.2c2.9-1.1 5.6-1.1 8.5.5 2.9-1.6 5.6-1.6 8.5-.5v11.4c-2.9-1.1-5.6-1.1-8.5.5-2.9-1.6-5.6-1.6-8.5-.5z" />
      <path d="M12 6.7v11.4" />
    </svg>
  );
}

function SearchIcon(): JSX.Element {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l4.5 4.5" />
    </svg>
  );
}

export function MenuIcon(): JSX.Element {
  return (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
      <path d="M3.5 6.5h17M3.5 12h17M3.5 17.5h17" />
    </svg>
  );
}

/** Emits a `guruji:conversations` event so the sidebar refreshes after a send or
 *  a new session without Chat importing the sidebar or the two sharing a store.
 *  A window event is the cheapest rung of the ladder for one cross-screen signal;
 *  a state library would be more machinery than the problem deserves. */
export function refreshConversations(): void {
  window.dispatchEvent(new CustomEvent("guruji:conversations"));
}

/** Rail state lives here, not in a store: one boolean, one consumer. Persisted per
 *  device like the theme — a preference about this screen on this machine, not
 *  something that should follow a student to a shared school computer. */
const RAIL_KEY = "guruji.rail";

function readRail(): boolean {
  try {
    return localStorage.getItem(RAIL_KEY) === "1";
  } catch {
    return false;
  }
}

export function Sidebar({
  onNavigate,
  rail = false,
  onToggleRail,
}: {
  onNavigate?: () => void;
  rail?: boolean;
  onToggleRail?: () => void;
}): JSX.Element {
  const [items, setItems] = useState<ConversationOut[] | null>(null);
  const [query, setQuery] = useState("");
  const route = useRoute();
  const s = session.get();

  async function load(): Promise<void> {
    try {
      setItems(await api.conversations(30));
    } catch {
      // A sidebar that cannot list is not a reason to break the chat. Empty
      // state below says so rather than showing a fake "no conversations".
      setItems(null);
    }
  }

  useEffect(() => {
    void load();
    const onRefresh = (): void => void load();
    window.addEventListener("guruji:conversations", onRefresh);
    return () => window.removeEventListener("guruji:conversations", onRefresh);
  }, []);

  async function remove(id: string, ev: Event): Promise<void> {
    ev.stopPropagation();
    // Optimistic: the row disappears immediately and the request follows. A
    // failed hide restores it on the next load rather than blocking the tap.
    setItems((cur) => cur?.filter((c) => c.id !== id) ?? cur);
    try {
      await api.hideConversation(id);
    } finally {
      void load();
    }
  }

  /* Client-side filter. The titles are already in memory and there are at most
     30 of them — a round trip per keystroke would be slower than the filter it
     replaces, and would fail offline. */
  const shown = (items ?? []).filter((c) =>
    query ? (c.title ?? "").toLowerCase().includes(query.toLowerCase()) : true,
  );

  function open(id: string): void {
    navigate(`/chat/${id}` as never);
    onNavigate?.();
  }

  function startNew(): void {
    // sessionStorage, not a route param: the flag is consumed once by Chat on
    // mount and must not survive a reload, or every refresh would silently open
    // a new conversation and the student's history would fill with empty rows.
    try {
      sessionStorage.setItem("guruji.newchat", "1");
    } catch {
      /* falls back to continuing the current session */
    }
    // "/chat", NOT "/start". /start is the ONBOARDING route: app.tsx renders
    // <Onboarding/> there whenever session.studentId is missing, which it often
    // is — studentId is only written at signup or by the profile screen's /me
    // call, so a returning student on a fresh browser has none. Tapping "New
    // chat" then dropped them into "Which class are you in?" as though they had
    // never signed up. The class pill has nothing to do with it.
    navigate("/chat");
    onNavigate?.();
  }

  return (
    <>
      <div class="side-brand">
        <button
          class="mark"
          onClick={onToggleRail}
          aria-label={rail ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!rail}
          title={rail ? "Expand sidebar" : "Collapse sidebar"}
        >
          <MarkGlyph />
        </button>
        <div>
          <b>GuruJi</b>
          <span>NCERT · Hinglish</span>
        </div>
      </div>

      <div class="side-actions">
        <button class="newchat" onClick={startNew} title="New chat" aria-label="New chat">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
          <span>New chat</span>
        </button>

        {rail ? (
          /* In the rail the field has nowhere to go, so the icon expands the
             sidebar and the effect then focuses the input — one tap, not two. */
          <button
            class="side-item"
            onClick={onToggleRail}
            title="Search chats"
            aria-label="Search chats"
          >
            <SearchIcon />
          </button>
        ) : (
          <label class="searchbar">
            <SearchIcon />
            <input
              type="search"
              value={query}
              placeholder="Search chats"
              aria-label="Search chats"
              onInput={(e: Event) => setQuery((e.currentTarget as HTMLInputElement).value)}
            />
          </label>
        )}
      </div>

      <div class="side-list">
        <div class="side-label">{query ? "Results" : "Recent"}</div>
        {items === null ? (
          <p class="note" style="padding:0 .5rem">
            Couldn't load your chats just now.
          </p>
        ) : shown.length === 0 ? (
          <p class="note" style="padding:0 .5rem">
            {query ? `Nothing matches "${query}".` : "Your past chats will appear here."}
          </p>
        ) : (
          shown.map((c) => (
            <div
              key={c.id}
              class="side-item"
              role="button"
              tabIndex={0}
              aria-current={route === `/chat/${c.id}` ? "true" : undefined}
              onClick={() => open(c.id)}
              onKeyDown={(e: KeyboardEvent) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  open(c.id);
                }
              }}
            >
              <span class="t">{c.title ?? "Untitled chat"}</span>
              <button
                class="x"
                aria-label={`Remove ${c.title ?? "this chat"}`}
                onClick={(e: Event) => void remove(c.id, e)}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
                  <path d="M5 7h14M10 11v6M14 11v6M6 7l1 12.5A1.5 1.5 0 0 0 8.5 21h7a1.5 1.5 0 0 0 1.5-1.5L18 7M9.5 7V5.2A1.2 1.2 0 0 1 10.7 4h2.6a1.2 1.2 0 0 1 1.2 1.2V7" />
                </svg>
              </button>
            </div>
          ))
        )}
      </div>

      {/* Bottom zone. Settings and identity belong at the far end of the rail,
          away from the primary actions — the flex spacer on .side-list is what
          pushes them there. */}
      <div class="side-foot">
        <button
          class="side-item"
          style={rail ? undefined : "flex:1"}
          aria-current={route === "/account" ? "true" : undefined}
          title="Your profile and settings"
          onClick={() => {
            navigate("/account");
            onNavigate?.();
          }}
        >
          <span class="avatar" aria-hidden="true">
            {s?.avatar ?? "🦉"}
          </span>
          <span class="t">{s?.displayName ?? "Your profile"}</span>
        </button>
        <ThemeToggle />
      </div>
    </>
  );
}

export function Shell({
  children,
  bare = false,
}: {
  children: ComponentChildren;
  bare?: boolean;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const [rail, setRail] = useState(readRail);
  const route = useRoute();

  function toggleRail(): void {
    setRail((v) => {
      try {
        localStorage.setItem(RAIL_KEY, v ? "0" : "1");
      } catch {
        /* preference still applies for this session */
      }
      return !v;
    });
  }

  // Close the drawer on navigation. Without this, tapping a conversation on a
  // phone leaves the drawer covering the chat you just opened.
  useEffect(() => setOpen(false), [route]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (bare) {
    // Auth, Onboarding and the parent portal have no chrome of their own, so the
    // theme control floats. Without this a student who prefers light mode signs
    // in through a dark screen and only sees their choice honoured afterwards.
    return (
      <div class="app">
        <div class="float-theme">
          <ThemeToggle />
        </div>
        {children}
      </div>
    );
  }

  return (
    <div class="app" data-shell="chat" data-side={rail ? "rail" : undefined}>
      {open ? <div class="scrim" onClick={() => setOpen(false)} /> : null}
      <aside class="side" data-open={open ? "true" : undefined}>
        <Sidebar onNavigate={() => setOpen(false)} rail={rail} onToggleRail={toggleRail} />
      </aside>
      <main class="main">
        <MenuContext.Provider value={() => setOpen(true)}>{children}</MenuContext.Provider>
      </main>
    </div>
  );
}

/* A one-value context beats prop-drilling `onMenu` through every screen, and is
   cheaper than adding a store for a single callback. */
import { createContext } from "preact";
export const MenuContext = createContext<() => void>(() => undefined);
