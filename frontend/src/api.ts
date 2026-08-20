import { RATE_LIMIT_PER_MIN } from "./backend";
import type {
  ConversationOut,
  Grade,
  LinkParentResponse,
  MessageOut,
  Role,
  SendMessageOut,
  StudentOut,
  FlaggedExchangeOut,
  StudentSummaryOut,
  TokenResponse,
  CurriculumSubject,
} from "./backend";
import { session } from "./session";

/**
 * Same-origin only. nginx proxies /api/* to the FastAPI container, so the
 * browser never makes a cross-origin request and:
 *   - there is no CORS preflight on the hot path (one fewer RTT per message on
 *     a high-latency mobile network),
 *   - the backend's ALLOWED_WEB_ORIGINS can stay empty, which is its safest
 *     setting,
 *   - `connect-src 'self'` in the CSP is enough to block exfiltration to an
 *     attacker-controlled host.
 */
const BASE = "/api";

/** A tutoring turn is a synchronous RAG + LLM call; the backend budgets 3-5s
 *  and its own OpenAI client times out at 10s with one retry. 35s covers the
 *  documented worst case (~21s) with margin, and still fails rather than
 *  hanging a spinner forever. */
const TIMEOUT_SLOW_MS = 35_000;
const TIMEOUT_MS = 12_000;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly retryAfterSec?: number,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

/** Thrown before a request leaves the browser. */
export class LocalLimitError extends Error {
  constructor(readonly waitSec: number) {
    super("local rate limit");
    this.name = "LocalLimitError";
  }
}

/* -------------------------------------------------------------------------
   Client-side mirror of the server's 20/min per-user limit.

   This is not a security control — the server limit is, and this one is
   trivially bypassed by anyone who opens devtools. It exists so a child who
   taps send repeatedly does not generate 40 requests that the single backend
   container has to authenticate, hit the database for, and reject. Under load
   the cheapest request is the one never sent.
   ------------------------------------------------------------------------- */
const sent: number[] = [];

function localLimitCheck(): void {
  const now = Date.now();
  while (sent.length && now - (sent[0] as number) > 60_000) sent.shift();
  if (sent.length >= RATE_LIMIT_PER_MIN) {
    const oldest = sent[0] as number;
    throw new LocalLimitError(Math.ceil((60_000 - (now - oldest)) / 1000));
  }
  sent.push(now);
}

/* ------------------------------------------------------------------------- */

interface Options {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  auth?: boolean;
  slow?: boolean;
}

async function request<T>(path: string, opts: Options = {}): Promise<T> {
  const { method = "GET", body, auth = true, slow = false } = opts;

  // FormData carries its own multipart Content-Type WITH a generated boundary.
  // Setting the header manually omits the boundary and the server cannot parse
  // the body, so this branch must leave it alone.
  const isForm = body instanceof FormData;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined && !isForm) headers["Content-Type"] = "application/json";
  if (auth) {
    const token = session.token();
    if (!token) throw new ApiError(401, "Not signed in");
    headers["Authorization"] = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      headers,
      ...(body !== undefined ? { body: isForm ? (body as FormData) : JSON.stringify(body) } : {}),
      // "same-origin", not "omit". The app's own auth is a bearer header and it
      // sets no cookies, so this looks like it should be "omit" — but credentials
      // in the fetch spec also cover HTTP authentication entries, and "omit"
      // stops the browser attaching the cached Basic credentials that the pilot
      // gate in front of this deployment requires. With "omit", the two OTP
      // calls below are rejected by the proxy and never reach the API at all,
      // which surfaces as a login that fails with nothing in the server log.
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error", // a redirect on an API path is a misconfiguration, not a flow
      signal: AbortSignal.timeout(slow ? TIMEOUT_SLOW_MS : TIMEOUT_MS),
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "TimeoutError") {
      throw new ApiError(0, "GuruJi took too long to answer. Try again.");
    }
    throw new ApiError(0, "No connection. Check your internet and try again.");
  }

  // 401 on an AUTHENTICATED call means the token is expired, revoked or forged.
  // Drop it here, once, rather than letting every screen invent its own sign-out
  // path.
  //
  // Gated on `auth` because the two unauthenticated calls — otp/request and
  // otp/verify — also answer 401, there meaning "wrong code". Treating that as a
  // dead session shows "Your session ended" to someone who never had one, and
  // clears a session that may be valid in another tab. Those callers report
  // their own error instead.
  if (res.status === 401 && auth) {
    session.clear();
    throw new ApiError(401, "Your session ended. Sign in again.");
  }

  if (res.status === 204) return undefined as T;

  let payload: unknown = null;
  try {
    payload = await res.json();
  } catch {
    /* empty or non-JSON body; handled below */
  }

  if (!res.ok) {
    const retry = Number(res.headers.get("Retry-After"));
    throw new ApiError(
      res.status,
      detailOf(payload, res.status),
      Number.isFinite(retry) && retry > 0 ? retry : undefined,
    );
  }

  return payload as T;
}

/** FastAPI returns `{detail: string}` for HTTPException and `{detail: [...]}`
 *  for Pydantic 422s. Handle both without ever rendering a raw object. */
function detailOf(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const d = (payload as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      const first = d[0] as { msg?: unknown } | undefined;
      if (first && typeof first.msg === "string") return first.msg;
    }
  }
  if (status >= 500) return "GuruJi is having trouble right now. Try again shortly.";
  return "That did not work. Try again.";
}

/* =========================================================================
   Endpoints. One function per route the backend actually exposes — nothing
   speculative, nothing that maps to a route that does not exist.
   ========================================================================= */

export const api = {
  /* --- auth ------------------------------------------------------------- */

  /** 501 while DEV_OTP_BYPASS is off: real OTP delivery is a Phase 2 item. */
  requestOtp: (phone_number: string) =>
    request<{ status: string; dev_hint?: string }>("/v1/auth/otp/request", {
      method: "POST",
      body: { phone_number },
      auth: false,
    }),

  verifyOtp: (phone_number: string, code: string, role: Role) =>
    request<TokenResponse>("/v1/auth/otp/verify", {
      method: "POST",
      body: { phone_number, code, role },
      auth: false,
    }),

  logout: () => request<void>("/v1/auth/logout", { method: "POST" }),

  /* --- student profile -------------------------------------------------- */

  createStudent: (grade: Grade) =>
    request<StudentOut>("/v1/students", {
      method: "POST",
      body: { grade, board: "NCERT", preferred_language: "hinglish" },
    }),

  getStudent: (id: string) => request<StudentOut>(`/v1/students/${enc(id)}`),

  setGrade: (id: string, grade: Grade) =>
    request<StudentOut>(`/v1/students/${enc(id)}`, { method: "PATCH", body: { grade } }),

  linkParent: (id: string, parent_phone_number: string) =>
    request<LinkParentResponse>(`/v1/students/${enc(id)}/link-parent`, {
      method: "POST",
      body: { parent_phone_number },
    }),

  verifyParentLink: (id: string, link_pin: string) =>
    request<void>(`/v1/students/${enc(id)}/verify-parent-link`, {
      method: "POST",
      body: { link_pin },
    }),

  updateProfile: (
    id: string,
    patch: { grade?: number; display_name?: string; avatar?: string; preferred_language?: string },
  ) => request<StudentOut>(`/v1/students/${enc(id)}`, { method: "PATCH", body: patch }),

  profile: (id: string) => request<StudentOut>(`/v1/students/${enc(id)}`),

  /** Resolves the signed-in student from the token alone. Without it, a browser
   *  that did not just go through onboarding has no student_id, so the profile
   *  screen cannot save and the parent invite is unavailable. */
  me: () => request<StudentOut>("/v1/students/me"),

  summary: (id: string) => request<StudentSummaryOut>(`/v1/students/${enc(id)}/summary`),

  /** Verified parents only. The one endpoint that returns verbatim child-written
   *  text — /summary stays transcript-free by design. 403 until the parent link
   *  is verified, and 403 for any student_id this parent is not linked to. */
  flagged: (id: string) => request<FlaggedExchangeOut[]>(`/v1/students/${enc(id)}/flagged`),

  /* --- conversation ----------------------------------------------------- */

  /**
   * The only paid endpoint. Deliberately NOT retried on failure: a retry here
   * can bill a second OpenAI call for a request that may have already
   * succeeded server-side, and the backend already retries the model call once
   * internally. Retrying a paid, non-idempotent write from the client is how a
   * spend cap gets hit by the error path itself.
   */
  /** `opts.newSession` closes whatever is open and starts clean (the New chat
   *  button). `opts.conversationId` continues a specific past conversation — the
   *  server re-checks that it belongs to this student, so a tampered id is a 404,
   *  not someone else's transcript. Passing both is a client bug; the server
   *  resolves conversationId first. */
  send: (
    text: string,
    opts: {
      newSession?: boolean;
      conversationId?: string;
      grade?: number;
      subject?: string;
    } = {},
  ) => {
    localLimitCheck();
    return request<SendMessageOut>("/v1/conversations/messages", {
      method: "POST",
      body: {
        text,
        ...(opts.newSession ? { new_session: true } : {}),
        ...(opts.conversationId ? { conversation_id: opts.conversationId } : {}),
        // Applied only when a conversation is created. The server ignores it on
        // an existing thread — changing class mid-conversation would re-answer
        // earlier turns from a different textbook.
        ...(opts.grade ? { grade: opts.grade } : {}),
        ...(opts.subject ? { subject: opts.subject } : {}),
      },
      slow: true,
    });
  },

  /** A photographed question.
   *
   *  The image is downscaled on this device first (see photo.ts) and is NOT
   *  stored anywhere by the server: it is transcribed to text, the text runs
   *  through the ordinary tutoring pipeline, and the bytes are dropped. The
   *  response carries `transcribed_text` so the chat can show the child what
   *  GuruJi read, which is the only way they can tell a misread from a wrong
   *  answer.
   *
   *  `slow: true` because this waits on a vision call ahead of the usual
   *  moderation, retrieval and tutoring calls. */
  sendPhoto: (
    image: Blob,
    opts: { newSession?: boolean; conversationId?: string } = {},
  ) => {
    localLimitCheck();
    const form = new FormData();
    form.append("photo", image, "question.jpg");
    if (opts.newSession) form.append("new_session", "true");
    if (opts.conversationId) form.append("conversation_id", opts.conversationId);
    return request<SendMessageOut>("/v1/conversations/photo", {
      method: "POST",
      body: form,
      slow: true,
    });
  },

  /** Soft delete — the server hides it from the student and keeps the transcript,
   *  because a flagged exchange must stay readable by a parent. The UI says
   *  "Remove", not "Delete for everyone", so the copy matches what happens. */
  hideConversation: (conversationId: string) =>
    request<void>(`/v1/conversations/${enc(conversationId)}`, { method: "DELETE" }),

  /** What is actually ingested. Cached by the caller; this changes only when a
   *  class is re-ingested, which is a manual operation a few times a year. */
  subjects: () => request<CurriculumSubject[]>("/v1/curriculum/subjects"),

  conversations: (limit = 20, offset = 0) =>
    request<ConversationOut[]>(`/v1/conversations?limit=${limit}&offset=${offset}`),

  messages: (conversationId: string) =>
    request<MessageOut[]>(`/v1/conversations/${enc(conversationId)}/messages`),
};

/** Path segments are UUIDs from the API, but encoding them costs nothing and
 *  removes any chance of a crafted value escaping the path. */
function enc(v: string): string {
  return encodeURIComponent(v);
}
