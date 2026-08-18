import type { Role } from "./backend";

/**
 * Session storage for the bearer token.
 *
 * THE TRADEOFF, STATED PLAINLY
 * ----------------------------
 * The safest place for a session credential is an httpOnly, SameSite=Strict
 * cookie, because JavaScript cannot read it and therefore XSS cannot steal it.
 * That is not available here: `POST /v1/auth/otp/verify` returns the token in a
 * JSON body and `get_current_user` reads an `Authorization: Bearer` header. Both
 * are backend behaviours, and the brief is explicit that backend logic does not
 * change.
 *
 * Given a token that must live in JavaScript, the ranking is:
 *   1. in-memory only     — safest; lost on every reload, so a child is logged
 *                           out each time they switch apps on a phone. Rejected
 *                           on usability grounds for this audience.
 *   2. sessionStorage     — chosen. Scoped to the one tab, cleared when the tab
 *                           closes, never shared with other tabs or windows,
 *                           and not readable from another origin.
 *   3. localStorage       — rejected. Survives forever and is shared across
 *                           every tab on the origin; on a shared family phone
 *                           that is a child staying signed in indefinitely.
 *
 * sessionStorage is still readable by injected script. The actual defence
 * against that is the strict Content-Security-Policy in nginx.conf plus the
 * absence of any HTML-injection sink in this codebase (no innerHTML, no
 * dangerouslySetInnerHTML — Preact escapes all interpolated text). Storage
 * choice is the last line here, not the first.
 *
 * The token TTL is 7 days server-side and `POST /v1/auth/logout` revokes it by
 * jti immediately, so a stolen token has a bounded and interruptible life.
 */

const KEY = "guruji.session.v1";

export interface Session {
  token: string;
  userId: string;
  role: Role;
  /**
   * Known only if this browser completed onboarding, because the backend has no
   * `GET /v1/students/me`. A returning student on a fresh browser has a working
   * chat and history but no reachable profile. See DOCUMENTATION, backend ask 1.
   */
  studentId?: string;
  /** Mirrored from the profile so the sidebar can render a name and glyph without a
   *  request on every mount. The students row is authoritative; this is a cache,
   *  refreshed whenever the profile screen saves. */
  displayName?: string;
  avatar?: string;
  /** Cached so the chat's empty state can pick class-appropriate prompts without
   *  a request on every mount. Authoritative copy is students.grade. */
  grade?: number;
}

let current: Session | null = read();
const watchers = new Set<() => void>();

function read(): Session | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<Session>;
    // Validate rather than trust: storage is attacker-writable in an XSS world,
    // and a malformed shape here would crash the app shell on boot.
    if (typeof v.token !== "string" || typeof v.userId !== "string") return null;
    if (v.role !== "student" && v.role !== "parent" && v.role !== "admin") return null;
    return {
      token: v.token,
      userId: v.userId,
      role: v.role,
      ...(typeof v.studentId === "string" ? { studentId: v.studentId } : {}),
      ...(typeof v.displayName === "string" ? { displayName: v.displayName } : {}),
      ...(typeof v.avatar === "string" ? { avatar: v.avatar } : {}),
      ...(typeof v.grade === "number" ? { grade: v.grade } : {}),
    };
  } catch {
    return null;
  }
}

function write(next: Session | null): void {
  current = next;
  try {
    if (next) sessionStorage.setItem(KEY, JSON.stringify(next));
    else sessionStorage.removeItem(KEY);
  } catch {
    // Private-mode Safari and storage-full both throw. The in-memory copy still
    // works for this tab, so the user is not blocked — they just lose the
    // session on reload. Failing silently is correct; there is no user action.
  }
  for (const w of watchers) w();
}

export const session = {
  get: (): Session | null => current,
  token: (): string | null => current?.token ?? null,

  start(t: { access_token: string; user_id: string; role: Role }): void {
    write({ token: t.access_token, userId: t.user_id, role: t.role });
  },

  /** Records the student id learned from `POST /v1/students`. */
  rememberStudent(studentId: string): void {
    if (!current) return;
    write({ ...current, studentId });
  },

  /** Caches the student id the moment /students/me resolves it, so the parent
   *  invite and profile save work on any device, not only the one that went through
   *  onboarding. */
  rememberStudentId(id: string): void {
    if (!current || current.studentId === id) return;
    write({ ...current, studentId: id });
  },

  rememberProfile(p: {
    displayName?: string | null;
    avatar?: string | null;
    grade?: number | null;
  }): void {
    if (!current) return;
    const next: Session = { ...current };
    // PARTIAL update: a key that is absent means "leave it alone", a key that is
    // explicitly null means "clear it". The previous version treated absent and
    // null the same, so Chat calling rememberProfile({ grade }) on mount silently
    // wiped the student's name and avatar from the session — the sidebar and chat
    // header would fall back to "Your profile" and the default owl until the
    // profile screen was opened again.
    if (p.grade !== undefined) {
      if (p.grade) next.grade = p.grade;
      else delete next.grade;
    }
    if (p.displayName !== undefined) {
      if (p.displayName) next.displayName = p.displayName;
      else delete next.displayName;
    }
    if (p.avatar !== undefined) {
      if (p.avatar) next.avatar = p.avatar;
      else delete next.avatar;
    }
    write(next);
  },

  clear(): void {
    write(null);
  },

  subscribe(fn: () => void): () => void {
    watchers.add(fn);
    return () => watchers.delete(fn);
  },
};
