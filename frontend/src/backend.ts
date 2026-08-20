/**
 * A deliberate mirror of values that live in the Python backend.
 *
 * System state arrives as a real `grounding` field, plus a `citation` when the
 * answer came from the textbook — never inferred from the reply text.
 *
 * The REPLY constants below survive for one reason: transcripts written before
 * those fields existed have `grounding: null`, and matching their text is the only
 * way to label them. New replies never take that path.
 *
 * Source: app/modules/ai_orchestrator/orchestrator.py  (TurnResult)
 *         app/modules/conversation/schemas.py          (SendMessageOut, MessageOut)
 */

export const REPLY = {
  /** orchestrator.FALLBACK_UNAVAILABLE — OpenAI errored or timed out. */
  UNAVAILABLE:
    "Arre yaar, GuruJi ka network abhi thoda slow hai. 5 minute baad phir try karo! \u{1F64F}",
  /** orchestrator.FALLBACK_MODERATED — inbound message flagged; exchange stored. */
  MODERATED:
    "Yeh sawal main answer nahi kar sakta, dost. Chalo padhai ki baat karte hain \u2014 kaunsa chapter chal raha hai?",
  /** orchestrator.FALLBACK_UNSAFE — the reply failed validation or output moderation. */
  UNSAFE:
    "Hmm, mujhse yeh theek se explain nahi ho paya. Ek baar phir se poochho, thoda alag words mein?",
  /** orchestrator.FALLBACK_BUDGET — daily spend circuit breaker tripped. */
  BUDGET: "GuruJi aaj ke liye thak gaya hai! Kal phir milte hain, pakka. \u{1F4DA}",
} as const;

/**
 * What the backend says about where an answer came from. Mirrors the docstring
 * on SendMessageOut.grounding.
 *   grounded    from the textbook; `citation` names the chapter
 *   weak        loosely related material; the reply hedges in its own words
 *   empty       searched, found nothing; the reply says so
 *   not_needed  greeting or language request — no search was run
 *   blocked     moderation, validation, spend cap, or provider outage
 */
export type Grounding =
  | "grounded"
  | "weak"
  | "empty"
  | "not_needed"
  | "blocked"
  | "n/a";

/** How a reply is labelled under the bubble. `answer` gets no label at all. */
export type ReplyState = "answer" | "resting" | "offline" | "held" | "unclear";

/**
 * LEGACY. Only for messages stored before `grounding` existed, which arrive
 * with `grounding: null` from the history endpoint. A live reply carries its
 * state as a field and must never come through here — see stateFor().
 */
export function classifyReply(reply: string): ReplyState {
  switch (reply) {
    case REPLY.BUDGET:
      return "resting";
    case REPLY.UNAVAILABLE:
      return "offline";
    case REPLY.MODERATED:
      return "held";
    case REPLY.UNSAFE:
      return "unclear";
    default:
      // Everything else is treated as a real tutoring reply and carries NO
      // provenance label. We deliberately do not guess whether an answer was
      // grounded in the textbook: the endpoint does not say, and inventing a
      // "from your textbook" badge for an answer we cannot verify would be a
      // lie about the one thing this product promises. See backend ask 2.
      return "answer";
  }
}

/**
 * The one function the UI should call. Prefers the field the backend actually
 * sent; falls back to string matching only for pre-migration rows.
 *
 * `blocked` deliberately maps to "unclear" rather than a state of its own: the
 * child does not need to know whether a reply was withheld by moderation, by
 * output validation, or by a spend cap. All three mean the same thing to them
 * — GuruJi did not answer this one — and the specific reason is an operator
 * concern that belongs in the log line, not on a 13-year-old's screen.
 */
export function stateFor(reply: string, grounding: Grounding | null | undefined): ReplyState {
  if (!grounding || grounding === "n/a") return classifyReply(reply);
  return grounding === "blocked" ? classifyReply(reply) : "answer";
}

/* -------------------------------------------------------------------------
   Limits mirrored from the backend so the UI can refuse locally instead of
   spending a request to be told no.
   ------------------------------------------------------------------------- */

/** conversation/router.py MAX_INBOUND_CHARS, and the web send_message() check. */
export const MAX_MESSAGE_CHARS = 2000;

/** config.RATE_LIMIT_PER_MIN — per user, per minute, in-process on one container. */
export const RATE_LIMIT_PER_MIN = 20;

/** schema.sql: students.grade CHECK (grade BETWEEN 5 AND 10). */
export const GRADES = [5, 6, 7, 8, 9, 10] as const;
export type Grade = (typeof GRADES)[number];

/** identity/router.py DEV_OTP_CODE. Only ever accepted while DEV_OTP_BYPASS=1. */
export const OTP_LENGTH = 6;

/* -------------------------------------------------------------------------
   Response shapes. Hand-written from the Pydantic schemas rather than
   generated: there are nine of them and a codegen step is a build dependency
   plus a drift surface for no gain at this size.
   ------------------------------------------------------------------------- */

export type Role = "student" | "parent" | "admin";

export interface TokenResponse {
  access_token: string;
  user_id: string;
  role: Role;
}

export interface StudentOut {
  id: string;
  grade: number;
  board: string;
  preferred_language: string;
  display_name: string | null;
  avatar: string | null;
}

export interface StudentSummaryOut {
  student_id: string;
  grade: number;
  board: string;
  total_messages: number;
  struggle_topics: string[];
  mastered_topics: string[];
}

export interface LinkParentResponse {
  parent_user_id: string;
  link_pin: string;
  verified: boolean;
}

export interface SendMessageOut {
  conversation_id: string;
  reply: string;
  grounding: Grounding;
  /** Present only when grounding is "grounded". e.g. "Science — Chapter 6: Pressure…" */
  citation: string | null;
  /** The passage the answer was built from. Lets a child CHECK the claim rather
   *  than trust the chapter label — the anti-hallucination pitch made verifiable. */
  source_excerpt: string | null;
  /** Photo questions only: what the vision model read off the image. Shown as the
   *  student's own message, because a child needs to be able to tell "GuruJi
   *  misread my handwriting" apart from "GuruJi got the answer wrong". */
  transcribed_text?: string | null;
}

/** Preset avatar glyphs. Not an upload: there is no object store, no lawful
 *  basis under the DPDP Rules to RETAIN a photograph of a minor, and a picker
 *  gives the child the same sense of ownership at none of that cost.
 *
 *  Photo questions are not a contradiction of this: that image is moderated,
 *  transcribed, and discarded within the request. It is never stored, so the
 *  retention question this comment is about never arises. */
export const AVATARS = [
  "🦉", "🚀", "🐯", "🌟", "🧠", "🎯",
  "🦁", "🐬", "⚡", "🌱", "🎨", "🏏",
] as const;

export interface ConversationOut {
  id: string;
  channel: "whatsapp" | "web";
  started_at: string;
  last_message_at: string | null;
  /** The student's own first message, used as the History label. Null on older rows. */
  title: string | null;
  closed_at: string | null;
  /** The class this chat is pinned to. null = follows the student's profile. */
  grade: number | null;
  /** The subject this chat is pinned to. null = every subject. */
  subject: string | null;
}

/** One (class, subject) pair that actually has embedded chunks. Derived from the
 *  corpus by the backend, never hardcoded — a menu offering a book GuruJi cannot
 *  open is the same failure as an opener for a chapter that was removed. */
export interface CurriculumSubject {
  grade: number;
  subject: string;
  chapters: number;
}

export interface MessageOut {
  id: string;
  sender: "student" | "assistant";
  content: string;
  created_at: string;
  /** 'photo' when this student message was transcribed from an image. The image
   *  itself is never stored, so on a reloaded transcript this is the only trace
   *  that the child sent a picture rather than typed. */
  source?: string | null;
  /** null on student messages and on anything stored before provenance existed. */
  grounding: Grounding | null;
  citation: string | null;
}

/** One exchange the moderation endpoint refused, for the parent view.
 *  `direction`: "inbound" is what the child typed; "outbound" is a GuruJi reply
 *  the output check caught before the child saw it. */
export interface FlaggedExchangeOut {
  id: number;
  direction: "inbound" | "outbound";
  content: string;
  flagged_at: string;
}
