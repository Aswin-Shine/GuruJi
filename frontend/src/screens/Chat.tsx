import type { JSX } from "preact";
import { useContext, useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api";
import { ACCEPTED_IMAGE_TYPES, downscale } from "../photo";
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
  /** Object URL for a photo sent in THIS session, so the student can see the
   *  picture they took next to what GuruJi read from it.
   *
   *  Session-scoped on purpose. The server never stores the image, so a reload
   *  reconstructs the transcript from text alone and this is undefined — the
   *  `fromPhoto` marker below is what survives. Making the picture outlive the
   *  session would mean retaining a photograph taken by a child, which is the
   *  thing the whole feature is built to avoid. */
  image?: string;
  /** True when this student message arrived as a photo. Set live from the upload
   *  and on reload from MessageOut.source, so the marker is consistent either
   *  way even though the image itself is not. */
  fromPhoto?: boolean;
}

/* ---------------------------------------------------------------------------
   Photo previews.

   MODULE scope, not component state or a ref, and this is the whole fix for the
   picture vanishing a second after it appeared.

   Sending the first photo navigates to the new conversation URL. That rebuilds
   the turn list from the server, which stores only the transcribed text — so
   anything held inside the component is gone by the time the list comes back,
   and a remount would additionally revoke the blob and leave a broken-image
   glyph on screen. Keeping the map outside the component makes it survive both.

   Keyed by conversation id + message text, the only thing both sides agree on:
   sendPhoto returns `transcribed_text` and the server stores exactly that as the
   student message.

   Lives for the tab, deliberately. A reload starts empty, so the picture is gone
   and the "Sent as a photo" marker takes over — the image was never stored
   server-side and is not meant to outlive the session.
--------------------------------------------------------------------------- */
const objectUrls = new Set<string>();

/* conversationId -> preview URLs, in the order they were sent.
   Keyed by POSITION, not by the transcribed text. Text looked like the obvious
   key and is not: the server may normalise, truncate at 2000 chars, or return a
   re-transcription that differs by a character, and any of those makes the
   lookup miss silently — the picture just quietly fails to come back. The nth
   photo message in a conversation is always the nth photo sent to it. */
const photoPreviews = new Map<string, string[]>();

function rememberPreview(convId: string, url: string): void {
  const list = photoPreviews.get(convId) ?? [];
  list.push(url);
  photoPreviews.set(convId, list);
}

/** Revoke every preview and forget them. Called when the session genuinely ends
 *  — sign-out — rather than on unmount, because navigating between chats
 *  unmounts too and that must not destroy a picture the student is still
 *  looking at. Blobs are not garbage collected while a URL exists, so this is
 *  what stops twenty photos pinning twenty full-size images in memory. */
export function clearPhotoPreviews(): void {
  objectUrls.forEach((u) => URL.revokeObjectURL(u));
  objectUrls.clear();
  photoPreviews.clear();
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
  // `soon: null` means the row is live. Photo is the only one built: the image is
  // transcribed to text on the server and discarded, so it is an input method
  // rather than a new answer source, and nothing downstream changes.
  // "Add", not "Take": on a laptop this opens the file browser, not a camera.
  // The same input serves both — `capture` is honoured by phones and ignored by
  // desktop browsers — so one row covers every device without branching.
  { key: "photo", label: "Add a photo of your question", soon: null },
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
     reshuffle under the student's finger on every re-render. Reseeded by the
     effect below whenever chatGrade or chatSubject actually changes. */
  const [prompts, setPrompts] = useState<string[]>(() => openersFor(s?.grade));
  /* The class for THIS chat. Defaults to the profile, but a sibling can switch it
     before their first message without overwriting the other child's profile. Only
     meaningful while the conversation is empty — once a message exists the class is
     stamped on the conversation server-side. */
  const [chatGrade, setChatGrade] = useState<number | undefined>(s?.grade);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const photoRef = useRef<HTMLInputElement>(null);
  // Highlights the log while a file is dragged over it. Counter, not a boolean:
  // dragenter/dragleave fire for every child element the pointer crosses, so a
  // plain flag flickers off the moment the cursor moves between two bubbles.
  const [dragDepth, setDragDepth] = useState(0);
  // Bound to the URL so Back works and a reload lands in the same conversation.
  const targetId = conversationParam() ?? undefined;
  /* Set just before navigating to a conversation we have JUST posted to.
     Sending the first message of a new chat calls navigate("/chat/<id>"), which
     re-runs the loader below and replaces the turns with the server's copy. That
     copy is correct for text and lossy for photos — the image was discarded
     during the request and only exists in this tab. Refetching what we already
     hold is a wasted round trip AND the thing that drops the picture, so the
     loader skips exactly once. */
  const skipNextLoad = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (!targetId) {
        setTurns([]);
        return;
      }
      if (skipNextLoad.current === targetId) {
        // We navigated here ourselves, moments ago, and the turns on screen are
        // already this conversation — including any photo previews.
        skipNextLoad.current = null;
        return;
      }
      try {
        const msgs = await api.messages(targetId);
        if (cancelled) return;
        // Re-attach previews this tab still holds, by position. The server sends
        // the transcript text only — the image was discarded during the request —
        // so without this, the navigate() that follows the first message of a new
        // chat silently drops the picture the student just sent.
        const previews = photoPreviews.get(targetId) ?? [];
        let photoIndex = 0;
        setTurns(
          msgs.map((m) => {
            const isPhoto = m.sender === "student" && m.source === "photo";
            // Undefined for a photo sent before a reload or from another device;
            // the marker then stands in for the picture. The key is OMITTED
            // rather than set to undefined, because exactOptionalPropertyTypes
            // treats an explicit undefined as a different thing from absence.
            const url = isPhoto ? previews[photoIndex++] : undefined;
            return {
              id: nextId(),
              from: m.sender === "student" ? "student" : "guruji",
              text: m.content,
              ...(m.sender === "assistant"
                ? {
                  state: stateFor(m.content, m.grounding),
                  grounding: m.grounding,
                  citation: m.citation,
                }
                : { fromPhoto: isPhoto, ...(url ? { image: url } : {}) }),
            };
          }),
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
  }, [s?.grade]);

  /* Reseed the opener pool whenever the EFFECTIVE class or subject changes —
     one place, not one per call site. Every prior version of this called
     setPrompts by hand from each handler that could change chatGrade or
     chatSubject, and it was missing from three of them (the catalog auto-pin
     below, and both subject-menu clicks), which is exactly how a student who
     explicitly picked Mathematics kept seeing Science openers: chatSubject
     updated, nothing re-ran openersFor(). Depending on the state itself instead
     of the handlers that set it means a future subject-setting call site gets
     this for free. */
  useEffect(() => {
    setPrompts(openersFor(chatGrade, chatSubject));
  }, [chatGrade, chatSubject]);

  /* Subjects available for the currently selected class. */
  const subjectsHere = (catalog ?? [])
    .filter((c) => c.grade === chatGrade)
    .map((c) => c.subject);

  function pickGrade(g: number): void {
    setChatGrade(g);
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

  function trackObjectUrl(url: string): void {
    objectUrls.add(url);
  }

  function releaseObjectUrl(url: string): void {
    if (objectUrls.delete(url)) URL.revokeObjectURL(url);
    // Also drop it from the per-conversation lists, or a failed upload leaves a
    // dead URL occupying a position and every later preview lands on the wrong
    // message.
    for (const [conv, list] of photoPreviews) {
      const i = list.indexOf(url);
      if (i !== -1) list.splice(i, 1);
      if (list.length === 0) photoPreviews.delete(conv);
    }
  }

  /** A preview whose blob is gone renders as a broken-image glyph next to its alt
   *  text, which is worse than showing nothing — it looks like the upload failed
   *  when the answer above it plainly worked. Drop the image and let the
   *  "Sent as a photo" marker take over, which is the honest state anyway. */
  function dropDeadPreview(turnId: number): void {
    setTurns((t) =>
      t.map((x) => {
        if (x.id !== turnId) return x;
        // The key is DELETED, not set to undefined. exactOptionalPropertyTypes
        // treats an explicit undefined as a different thing from an absent
        // property, and `{...x, image: undefined}` would not even typecheck.
        const { image: _dead, ...rest } = x;
        return rest;
      }),
    );
  }


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
    const newSession = takeNewSessionFlag();

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
      if (!targetId) {
        skipNextLoad.current = r.conversation_id;
        navigate(`/chat/${r.conversation_id}`, true);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Message didn't send.");
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  /** Consumed once. Shared by send() and sendPhoto() so a photo taken right after
   *  "New chat" starts the new thread rather than appending to the old one. */
  function takeNewSessionFlag(): boolean {
    try {
      const v = sessionStorage.getItem("guruji.newchat") === "1";
      if (v) sessionStorage.removeItem("guruji.newchat");
      return v;
    } catch {
      return false;
    }
  }

  /** The one gate every image entry point goes through, whatever the gesture.
   *
   *  Only images. A dropped PDF or .docx is rejected here rather than uploaded
   *  and refused by the server, so the child is told why while the file is still
   *  in front of them. */
  function acceptImage(file: File | null | undefined): void {
    if (!file) return;
    if (!ACCEPTED_IMAGE_TYPES.includes(file.type)) {
      setError("Sirf photo bhej sakte ho — JPG, PNG ya WebP.");
      return;
    }
    void sendPhoto(file);
  }

  /** Ctrl/Cmd-V of a screenshot.
   *
   *  The natural desktop flow for a question on screen is the snipping tool then
   *  paste — there is no file on disk to browse to. Without this, a child on a
   *  computer has to save the screenshot somewhere first and then find it again.
   *
   *  Deliberately does NOT intercept a text paste: the composer must keep working
   *  normally, so this only acts when the clipboard actually carries an image. */
  useEffect(() => {
    function onPaste(e: ClipboardEvent): void {
      const item = Array.from(e.clipboardData?.items ?? []).find((i) =>
        i.type.startsWith("image/"),
      );
      if (!item) return;
      e.preventDefault();
      acceptImage(item.getAsFile());
    }
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  });

  async function sendPhoto(file: File): Promise<void> {
    if (busy) return;
    setError(null);
    setBusy(true);

    // A placeholder turn, because downscale + upload + vision + tutoring is the
    // longest wait in this app. Without it the screen sits unchanged for several
    // seconds after the camera closes and the tap looks like it did nothing.
    const placeholderId = nextId();
    // Preview from the ORIGINAL file, shown immediately — before downscaling and
    // before the upload. The student sees what they photographed while the round
    // trip runs, instead of several blank seconds.
    const previewUrl = URL.createObjectURL(file);
    trackObjectUrl(previewUrl);
    setTurns((t) => [
      ...t,
      { id: placeholderId, from: "student", text: "", image: previewUrl, fromPhoto: true },
    ]);

    const newSession = takeNewSessionFlag();
    try {
      const image = await downscale(file);
      const r = await api.sendPhoto(image, {
        ...(newSession ? { newSession: true } : {}),
        ...(targetId ? { conversationId: targetId } : {}),
      });
      // Recorded BEFORE setTurns, so the history reload that `navigate` triggers
      // below already finds it. Registering afterwards loses that race and the
      // picture disappears on exactly the first photo of a conversation.
      //
      // Unconditional: a transcription that came back empty is still a photo the
      // student sent and is still the nth photo in this conversation. Skipping it
      // would shift every later preview onto the wrong message.
      rememberPreview(r.conversation_id, previewUrl);

      setTurns((t) => [
        // The photo STAYS; the transcription is added beneath it. The picture is
        // what the child sent, the text is what GuruJi understood, and seeing
        // both is the only way to tell a misread of their handwriting from a
        // wrong answer.
        ...t.map((turn) =>
          turn.id === placeholderId && r.transcribed_text
            ? { ...turn, text: r.transcribed_text }
            : turn,
        ),
        {
          id: nextId(),
          from: "guruji" as const,
          text: r.reply,
          state: stateFor(r.reply, r.grounding),
          grounding: r.grounding,
          citation: r.citation,
          excerpt: r.source_excerpt,
        },
      ]);
      refreshConversations();
      if (!targetId) {
        skipNextLoad.current = r.conversation_id;
        navigate(`/chat/${r.conversation_id}`, true);
      }
    } catch (err) {
      // Drop the placeholder and release its preview. Leaving the photo above an
      // error reads as though it was sent and then failed downstream.
      setTurns((t) => t.filter((turn) => turn.id !== placeholderId));
      releaseObjectUrl(previewUrl);
      setError(err instanceof Error ? err.message : "That photo didn't send.");
    } finally {
      setBusy(false);
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
                          `g === 5 ? "EVS"`. A single subject is still named here,
                          because picking the class is the only click needed to
                          land on it. Two or more show nothing — that list is one
                          click away in the SUBJECT section below once this class
                          is selected, so repeating a count here is a badge that
                          says nothing a click wouldn't show immediately after. */}
                      {subs.length === 0 ? (
                        <em data-tone="none">soon</em>
                      ) : subs.length === 1 ? (
                        <em>{subs[0]!.subject}</em>
                      ) : null}
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

      {/* Drag and drop onto the conversation. The browser's default for a dropped
          image is to NAVIGATE to it, replacing the app — so preventDefault on
          dragover is not decoration, it is what stops a mis-drop losing the chat. */}
      <div
        class="scroll"
        ref={scrollRef}
        data-dropping={dragDepth > 0 ? "true" : undefined}
        onDragEnter={(e: DragEvent) => {
          if (!e.dataTransfer?.types.includes("Files")) return;
          e.preventDefault();
          setDragDepth((d) => d + 1);
        }}
        onDragOver={(e: DragEvent) => {
          if (e.dataTransfer?.types.includes("Files")) e.preventDefault();
        }}
        onDragLeave={() => setDragDepth((d) => Math.max(0, d - 1))}
        onDrop={(e: DragEvent) => {
          if (!e.dataTransfer?.types.includes("Files")) return;
          e.preventDefault();
          setDragDepth(0);
          acceptImage(e.dataTransfer.files?.[0]);
        }}
      >
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
              {t.image ? (
                <img
                  class="photo"
                  src={t.image}
                  alt="The question you photographed"
                  /* No loading="lazy": the blob is already in memory, there is
                     nothing to defer, and a deferred load is one more way for a
                     revoked URL to surface as a broken glyph. */
                  onError={() => dropDeadPreview(t.id)}
                />
              ) : null}
              {/* A photo turn has no text until the transcription comes back, and
                  an empty bubble under the picture is just a grey box. */}
              {t.text ? <div class="bubble">{t.text}</div> : null}
              {/* Survives a reload, unlike the picture. Without it a transcript
                  read later shows text the child never typed, with nothing to say
                  where it came from. */}
              {t.fromPhoto && !t.image ? (
                <span class="rail" data-state="hold">Sent as a photo</span>
              ) : null}
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
                        if (t.soon) say(t.soon);
                        else if (t.key === "photo") photoRef.current?.click();
                      }}
                    >
                      <ToolIcon name={t.key} />
                      <span>{t.label}</span>
                      {t.soon ? <em>Soon</em> : null}
                    </button>
                  ))}
                </div>
              ) : null}
              {/* capture="environment" asks a phone for the rear camera directly,
                  which is the whole point on the device this product targets. On a
                  desktop it degrades to an ordinary file picker. */}
              <input
                ref={photoRef}
                type="file"
                accept={ACCEPTED_IMAGE_TYPES.join(",")}
                capture="environment"
                hidden
                onChange={(e: Event) => {
                  const input = e.currentTarget as HTMLInputElement;
                  const file = input.files?.[0];
                  // Reset first: without this, picking the SAME file twice fires
                  // no change event and the second attempt silently does nothing.
                  input.value = "";
                  acceptImage(file);
                }}
              />
              <button
                class="tool"
                aria-label="Add a photo, note or voice"
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
