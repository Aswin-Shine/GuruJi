import type { JSX } from "preact";
import { useContext, useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api";
import { MAX_MESSAGE_CHARS, stateFor, type Grounding, type ReplyState } from "../backend";
import type { CurriculumSubject } from "../backend";
import { openersFor } from "../openers";
import { MenuContext, MenuIcon, refreshConversations, ThemeToggle } from "../components/Shell";
import { Banner } from "../components/ui";
import { conversationParam, navigate, useRoute } from "../router";
import { session } from "../session";

/**
 * The tutoring surface.
 *
 * Layout note, because this is the marked screenshot problem: the composer is NOT
 * pushed to the viewport floor. `.scroll` flexes and owns the slack, so a
 * two-message conversation sits at the top with the composer directly beneath it.
 * The old layout gave the composer an auto top margin, which on a 900px-tall
 * desktop window left a screen of empty space in the middle of the app.
 */

interface Turn {
  id: number;
  from: "student" | "guruji";
  text: string;
  state?: ReplyState;
  grounding?: Grounding | null;
  citation?: string | null;
  excerpt?: string | null;
}

let seq = 0;
const nextId = (): number => ++seq;

/* Openers now come from openers.ts, keyed by the student's class — see the note
   there on why they are hand-written rather than derived from chapter titles. */

const GREETING =
  "Koi bhi doubt poochho — main sirf tumhari NCERT kitaab se jawaab dunga.";

const RAIL: Record<Exclude<ReplyState, "answer">, { text: string; tone?: "hold" | "fault" }> = {
  resting: { text: "GuruJi is resting for today", tone: "hold" },
  offline: { text: "Couldn't reach GuruJi", tone: "fault" },
  held: { text: "Held back — try asking differently", tone: "hold" },
  unclear: { text: "GuruJi didn't answer this one", tone: "hold" },
};

/** Splits "Class 8 Science — Chapter 6: Pressure, Winds…" into a short marker and
 *  a name so the chip can weight them differently.
 *
 *  The marker carries the CLASS, because every class has a Chapter 6: a bare "Ch 6"
 *  hides the case that matters most, an answer retrieved from a lower class than the
 *  student is in. Degrades to the whole string when the citation has no recognisable
 *  shape. */
function splitCitation(citation: string): { mark: string; name: string } {
  const m = /(?:Class\s+(\d+)\s+)?.*?Chapter\s+(\d+)\s*:\s*(.+)$/.exec(citation);
  const cls = m?.[1];
  const no = m?.[2];
  const name = m?.[3];
  if (!no || !name) return { mark: "Book", name: citation };
  return { mark: cls ? `Class ${cls} · Ch ${no}` : `Ch ${no}`, name };
}

/* Composer tools, behind ONE trigger.
   Four bare icon buttons sitting above the input read as clutter and, worse, gave
   no clue what any of them did — a row of glyphs with amber dots is decoration,
   not an affordance. Collapsed into a single "+" that opens a labelled menu: each
   row now says what it is in words and carries a "Soon" tag, which is both tidier
   and more honest than a dot nobody can decode.

   Still not disabled. A greyed-out control tells someone the feature is broken; a
   live one that answers in words tells them it is scheduled. */
const TOOLS = [
  { key: "photo", label: "Take a photo of your question", soon: "Photo questions arrive with the pilot." },
  { key: "attach", label: "Attach a file", soon: "File attachments arrive with the pilot." },
  { key: "note", label: "Save a note", soon: "Notes arrive with the pilot." },
  { key: "voice", label: "Ask with your voice", soon: "Voice questions arrive with the pilot." },
] as const;

function ToolIcon({ name }: { name: string }): JSX.Element {
  // Each entry is a LIST of subpaths. The photo icon needs a body and a separate
  // lens circle; emitting only the first `d` rendered it as a plain rounded box.
  const p: Record<string, string[]> = {
    attach: [
      "M16.5 8.5 9.7 15.3a2.4 2.4 0 0 0 3.4 3.4l6.8-6.8a4.3 4.3 0 0 0-6.1-6.1l-6.8 6.8a6.2 6.2 0 0 0 8.8 8.8l5.6-5.6",
    ],
    photo: [
      "M4 8.5h2.8l1.4-2h7.6l1.4 2H20a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 20 19.5H4A1.5 1.5 0 0 1 2.5 18v-8A1.5 1.5 0 0 1 4 8.5Z",
      "M12 16.1a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
    ],
    note: ["M6 3.5h8.5L19 8v12.5H6z", "M14 3.5V8h5", "M9 12.5h6M9 16h4"],
    voice: [
      "M12 3.8a2.7 2.7 0 0 1 2.7 2.7v5.2a2.7 2.7 0 0 1-5.4 0V6.5A2.7 2.7 0 0 1 12 3.8Z",
      "M6 11.2a6 6 0 0 0 12 0",
      "M12 17.4v2.8",
    ],
  };
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      {(p[name] ?? []).map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}

/** The passage behind an answer — the product's whole claim made checkable by a
 *  13-year-old. Not "trust me, it's Chapter 6" but the sentence itself. */
function PassageSheet({
  citation,
  excerpt,
  onClose,
}: {
  citation: string;
  excerpt: string;
  onClose: () => void;
}): JSX.Element {
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      class="sheet-scrim"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Textbook passage"
    >
      <div class="sheet" onClick={(e: Event) => e.stopPropagation()}>
        <div class="sheet-grip" />
        <p class="src">From your textbook</p>
        <h2>{citation}</h2>
        <blockquote>{excerpt}</blockquote>
        <p class="note">
          This is the exact passage GuruJi read before answering. If the answer says something
          this passage doesn't, tell your teacher — GuruJi isn't supposed to add things.
        </p>
        <button class="btn" style="margin-top:var(--gap-3)" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}

export function Chat(): JSX.Element {
  const route = useRoute();
  const openMenu = useContext(MenuContext);
  const s = session.get();

  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [passage, setPassage] = useState<{ citation: string; excerpt: string } | null>(null);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [classOpen, setClassOpen] = useState(false);
  /* What the corpus actually contains. Fetched once; a class is re-ingested a
     few times a year, so this does not need to be live. */
  const [catalog, setCatalog] = useState<CurriculumSubject[] | null>(null);
  const [chatSubject, setChatSubject] = useState<string | undefined>(undefined);
  /* Chosen once per mount, so a new chat gets a different three but they do not
     reshuffle under the student's finger on every re-render. */
  const [prompts, setPrompts] = useState<string[]>(() => openersFor(s?.grade));
  /* The class for THIS chat. Defaults to the profile, but a sibling can switch it
     before their first message without overwriting the other child's profile. Only
     meaningful while the conversation is empty — once a message exists the class is
     stamped on the conversation server-side. */
  const [chatGrade, setChatGrade] = useState<number | undefined>(s?.grade);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Bound to the URL so Back works and a reload lands in the same conversation.
  const targetId = conversationParam() ?? undefined;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (!targetId) {
        setTurns([]);
        return;
      }
      try {
        const msgs = await api.messages(targetId);
        if (cancelled) return;
        setTurns(
          msgs.map((m) => ({
            id: nextId(),
            from: m.sender === "student" ? "student" : "guruji",
            text: m.content,
            ...(m.sender === "assistant"
              ? {
                state: stateFor(m.content, m.grounding),
                grounding: m.grounding,
                citation: m.citation,
              }
              : {}),
          })),
        );
      } catch {
        if (!cancelled) setError("Couldn't open that chat.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [route, targetId]);

  /* Follow a class change. The pool is seeded once in useState, so switching
     class in the profile and coming back would otherwise keep showing the old
     class's openers until a reload. */
  useEffect(() => {
    if (!s?.grade) return;
    setChatGrade((g) => g ?? s.grade);
    setPrompts(openersFor(chatGrade ?? s.grade));
  }, [s?.grade]);

  /* Subjects available for the currently selected class. */
  const subjectsHere = (catalog ?? [])
    .filter((c) => c.grade === chatGrade)
    .map((c) => c.subject);

  function pickGrade(g: number): void {
    setChatGrade(g);
    setPrompts(openersFor(g));
    // Changing class can invalidate the subject — Class 5 has EVS, Class 8 does
    // not. Clearing rather than guessing: null means "every subject", which is
    // the honest default when we no longer know what they meant.
    const next = (catalog ?? []).filter((c) => c.grade === g).map((c) => c.subject);
    setChatSubject(next.length === 1 ? next[0] : undefined);
  }

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const cat = await api.subjects();
        if (cancelled) return;
        setCatalog(cat);
        const here = cat.filter((c) => c.grade === (chatGrade ?? 0)).map((c) => c.subject);
        // With one subject in a class there is nothing to choose — pin it
        // silently rather than rendering a menu with a single option.
        if (here.length === 1) setChatSubject(here[0]);
      } catch {
        /* no catalog: subject stays null, retrieval searches every subject —
           exactly the behaviour before this feature existed */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chatGrade]);

  useEffect(() => {
    /* A browser that signed in but never opened the profile has no cached grade,
       so the openers would silently fall back to Class 8 — the exact bug this
       change exists to fix. One request, once, then cached in the session. */
    if (s?.grade) return;
    let cancelled = false;
    void (async () => {
      try {
        const me = await api.me();
        if (cancelled) return;
        session.rememberProfile({ grade: me.grade });
        setChatGrade((g) => g ?? me.grade);
        setPrompts(openersFor(me.grade));
      } catch {
        /* keep the fallback openers; a failed profile lookup must not break chat */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [s?.grade]);

  useEffect(() => {
    if (!classOpen) return;
    const close = (e: Event): void => {
      if (!(e.target as HTMLElement)?.closest?.(".classmenu")) setClassOpen(false);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setClassOpen(false);
    };
    document.addEventListener("pointerdown", close);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [classOpen]);

  useEffect(() => {
    if (!toolsOpen) return;
    const close = (e: Event): void => {
      // Anything outside the menu closes it, including a tap on the textarea —
      // a popover that survives the next interaction is a popover in the way.
      if (!(e.target as HTMLElement)?.closest?.(".tools")) setToolsOpen(false);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setToolsOpen(false);
    };
    document.addEventListener("pointerdown", close);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [toolsOpen]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({
      top: el.scrollHeight,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    });
  }, [turns.length, busy]);

  function say(msg: string): void {
    setToast(msg);
    window.setTimeout(() => setToast(null), 2600);
  }

  async function send(raw?: string): Promise<void> {
    const body = (raw ?? text).trim();
    if (!body || busy) return;
    if (body.length > MAX_MESSAGE_CHARS) {
      setError(`That's a bit long — keep it under ${MAX_MESSAGE_CHARS} characters.`);
      return;
    }
    setError(null);
    setText("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    setTurns((t) => [...t, { id: nextId(), from: "student", text: body }]);
    setBusy(true);

    // Consumed once. If this survived a reload, every refresh would silently open
    // a new conversation and fill the student's history with empty rows.
    let newSession = false;
    try {
      newSession = sessionStorage.getItem("guruji.newchat") === "1";
      if (newSession) sessionStorage.removeItem("guruji.newchat");
    } catch {
      /* continue in the current session */
    }

    try {
      const r = await api.send(body, {
        ...(newSession ? { newSession: true } : {}),
        ...(targetId ? { conversationId: targetId } : {}),
        // Only sent when this screen is starting a fresh thread. On an existing
        // conversation the server ignores it anyway.
        ...(!targetId && chatGrade ? { grade: chatGrade } : {}),
        ...(!targetId && chatSubject ? { subject: chatSubject } : {}),
      });
      setTurns((t) => [
        ...t,
        {
          id: nextId(),
          from: "guruji",
          text: r.reply,
          state: stateFor(r.reply, r.grounding),
          grounding: r.grounding,
          citation: r.citation,
          excerpt: r.source_excerpt,
        },
      ]);
      refreshConversations();
      // Bind to the conversation the server actually used, so the next message
      // continues it rather than re-triggering the 4-hour rule.
      if (!targetId) navigate(`/chat/${r.conversation_id}`, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Message didn't send.");
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  const empty = turns.length === 0;

  return (
    <>
      <header class="topbar">
        <button class="icon-btn only-mobile" onClick={openMenu} aria-label="Open menu">
          <MenuIcon />
        </button>
        <div class="grow">
          <h1>GuruJi</h1>
          <p>{s?.displayName ? `${s.displayName} · NCERT` : "NCERT · Hinglish"}</p>
        </div>

        {/* Class for this chat. In the topbar rather than the empty state: a
            segmented control is a settings-panel component, and dropping one into
            a greeting screen reads as a form in the middle of a conversation.
            Here it is a quiet pill in the chrome — always reachable, never in the
            way, and it uses the same trigger + popover pattern as the composer
            tools rather than inventing a third menu shape.

            Editable only while the conversation is empty. Once a message exists
            the class is stamped server-side, so an editable control would be
            offering something the backend correctly refuses. */}
        {empty ? (
          <div class="classmenu">
            {classOpen ? (
              <div class="tools-menu" role="menu" aria-label="Class for this chat">
                {[5, 6, 7, 8, 9, 10].map((g) => {
                  const subs = (catalog ?? []).filter((c) => c.grade === g);
                  return (
                    <button
                      class="tools-item"
                      key={g}
                      role="menuitem"
                      aria-current={chatGrade === g}
                      onClick={() => {
                        pickGrade(g);
                        if (subs.length <= 1) setClassOpen(false);
                      }}
                    >
                      <span>Class {g}</span>
                      {/* The subject label comes from the corpus, not a hardcoded
                          `g === 5 ? "EVS"`. When Class 5 Maths is ingested this
                          becomes "EVS +1" with no frontend change; when a class
                          has none it says so instead of implying a book exists. */}
                      {subs.length === 0 ? (
                        <em data-tone="none">soon</em>
                      ) : subs.length === 1 ? (
                        <em>{subs[0]!.subject}</em>
                      ) : (
                        <em>{subs.length} subjects</em>
                      )}
                    </button>
                  );
                })}

                {/* Subject row appears only when the chosen class genuinely has
                    more than one. A menu with a single option is noise pretending
                    to be a choice — today every class has exactly one subject, so
                    this renders nothing and appears by itself the day Maths lands. */}
                {subjectsHere.length > 1 ? (
                  <div class="menu-sub">
                    <span>Subject</span>
                    <button
                      class="tools-item"
                      role="menuitem"
                      aria-current={chatSubject === undefined}
                      onClick={() => {
                        setChatSubject(undefined);
                        setClassOpen(false);
                      }}
                    >
                      <span>All subjects</span>
                    </button>
                    {subjectsHere.map((sub) => (
                      <button
                        class="tools-item"
                        key={sub}
                        role="menuitem"
                        aria-current={chatSubject === sub}
                        onClick={() => {
                          setChatSubject(sub);
                          setClassOpen(false);
                        }}
                      >
                        <span>{sub}</span>
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
            <button
              class="classpill"
              aria-haspopup="menu"
              aria-expanded={classOpen}
              onClick={() => setClassOpen((v) => !v)}
              title="Which class is this chat for?"
            >
              Class {chatGrade ?? "—"}
              {/* When a class has several subjects the pill always says which one
                  is in play, including "All". Hiding it would mean the student
                  cannot tell a Science-scoped chat from an unscoped one, and the
                  unscoped case is exactly where cross-subject answers come from. */}
              {subjectsHere.length > 1 ? ` · ${chatSubject ?? "All"}` : ""}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
          </div>
        ) : null}
        <div class="only-mobile">
          <ThemeToggle />
        </div>
      </header>

      <div class="scroll" ref={scrollRef}>
        <div class="pane log">
          {empty ? (
            <div class="empty screen-in">
              <div class="seal" aria-hidden="true">
                गु
              </div>
              <h2>{s?.displayName ? `Namaste, ${s.displayName}!` : "Namaste!"}</h2>
              <p>{GREETING}</p>
            </div>
          ) : null}

          {turns.map((t) => (
            <div class="turn" data-from={t.from} key={t.id}>
              <div class="bubble">{t.text}</div>
              {t.state && t.state !== "answer" ? (
                <span class="rail" data-state={RAIL[t.state].tone}>
                  {RAIL[t.state].text}
                </span>
              ) : t.citation ? (
                /* Any chatbot can produce the paragraph above. Only one grounded
                   in a real textbook can name the chapter — and, when an excerpt
                   came back, let the child read the passage and check it.
                   Rendered only from server data; never inferred, because a false
                   provenance claim is worse than no claim. */
                <button
                  class="cite"
                  title={t.excerpt ? "See this in your textbook" : t.citation}
                  onClick={() => {
                    const c = t.citation;
                    if (t.excerpt && c) setPassage({ citation: c, excerpt: t.excerpt });
                    else say("The passage isn't available for this answer.");
                  }}
                >
                  <b>{splitCitation(t.citation).mark}</b>
                  {splitCitation(t.citation).name}
                </button>
              ) : t.grounding === "weak" ? (
                <span class="rail" data-state="hold">
                  Related — not from your chapter
                </span>
              ) : null}
            </div>
          ))}

          {busy ? (
            <div class="turn" data-from="guruji">
              <div class="think" role="status" aria-label="GuruJi is thinking">
                <i />
                <i />
                <i />
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <div class="composer">
        <div class="pane">
          {error ? <Banner tone="fault">{error}</Banner> : null}
          {toast ? <Banner tone="hold">{toast}</Banner> : null}

          {empty ? (
            <div class="chipbar">
              {prompts.map((p) => (
                <button class="chip" key={p} onClick={() => void send(p)}>
                  {p}
                </button>
              ))}
            </div>
          ) : null}

          <div class="ask">
            <div class="tools">
              {toolsOpen ? (
                <div class="tools-menu" role="menu" aria-label="Add to your question">
                  {TOOLS.map((t) => (
                    <button
                      class="tools-item"
                      key={t.key}
                      role="menuitem"
                      onClick={() => {
                        setToolsOpen(false);
                        say(t.soon);
                      }}
                    >
                      <ToolIcon name={t.key} />
                      <span>{t.label}</span>
                      <em>Soon</em>
                    </button>
                  ))}
                </div>
              ) : null}
              <button
                class="tool"
                aria-label="Add a photo, file, note or voice"
                aria-haspopup="menu"
                aria-expanded={toolsOpen}
                title="Add to your question"
                onClick={() => setToolsOpen((v) => !v)}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
                  <path d="M12 5.5v13M5.5 12h13" />
                </svg>
              </button>
            </div>
            <textarea
              ref={inputRef}
              rows={1}
              value={text}
              placeholder="Kuch bhi poochho…"
              aria-label="Your question"
              maxLength={MAX_MESSAGE_CHARS}
              onInput={(e: Event) => {
                const el = e.currentTarget as HTMLTextAreaElement;
                setText(el.value);
                // Grow to fit, capped — a textarea that grows without limit eats
                // the conversation it belongs to.
                el.style.height = "auto";
                el.style.height = `${Math.min(el.scrollHeight, 132)}px`;
              }}
              onKeyDown={(e: KeyboardEvent) => {
                // Enter sends, Shift+Enter breaks a line — but only on a real
                // keyboard. On a phone the return key must insert a newline, or
                // every press fires a half-written question.
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !window.matchMedia("(pointer: coarse)").matches
                ) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <button
              class="send"
              onClick={() => void send()}
              disabled={busy || text.trim().length === 0}
              aria-label="Send"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2.2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {passage ? (
        <PassageSheet
          citation={passage.citation}
          excerpt={passage.excerpt}
          onClose={() => setPassage(null)}
        />
      ) : null}
    </>
  );
}
