import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { ApiError, api } from "../api";
import { GRADES, type Grade } from "../backend";
import { Banner } from "../components/ui";
import { navigate } from "../router";
import { session } from "../session";

/**
 * One question, because grade is the only field retrieval actually needs:
 * `match_chunks()` filters on grade and subject, and subject is inferred from
 * the message. Name and "topic of interest" from the Founder Workbook's
 * three-question flow are personalisation with no effect on correctness, and
 * asking for them would need per-user turn state that Phase 1 does not have.
 *
 * Shown ONLY to accounts with no profile. Auth already routes returning students
 * straight to chat; this screen re-checks because /start is reachable by reload,
 * bookmark, or typed URL, and asking a registered student their class again is a
 * question with nothing behind it — POST /v1/students 409s once a profile exists,
 * and the class pill in chat is what actually changes a chat's class.
 */
export function Onboarding(): JSX.Element {
  const [grade, setGrade] = useState<Grade | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  /* Resolve the id and cache it, so a returning student's profile screen and
     parent invite work on this device too. */
  function adopt(me: { id: string; grade: number; display_name: string | null; avatar: string | null }): void {
    session.rememberStudentId(me.id);
    session.rememberProfile({ grade: me.grade, displayName: me.display_name, avatar: me.avatar });
  }

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const me = await api.me();
        if (cancelled) return;
        adopt(me);
        navigate("/chat", true);
      } catch {
        // No profile, or the lookup failed. Either way the form is the safe thing
        // to show: a first-time student must not be stranded by a flaky network,
        // and a registered one hits the 409 branch in begin() and lands in chat.
        if (!cancelled) setChecking(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function begin(): Promise<void> {
    if (!grade) return;
    setBusy(true);
    setError(null);
    try {
      const s = await api.createStudent(grade);
      session.rememberStudent(s.id);
      navigate("/chat", true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // The account already has a profile, so the answer just given is discarded
        // — the existing grade stands. Resolve the real id before leaving, or this
        // browser keeps a session with no student_id and the profile screen and
        // parent invite stay unreachable.
        try {
          adopt(await api.me());
        } catch {
          /* chat and history work without it; the profile screen retries on mount */
        }
        navigate("/chat", true);
        return;
      }
      setError(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  /* Deliberately not the form. Rendering it while the check is in flight would
     flash "Which class are you in?" at a student who has answered it already. */
  if (checking) {
    return (
      <main class="shell pad stack" style="gap:1.25rem">
        <div class="grow" />
        <p class="lede" style="text-align:center">Just a moment…</p>
        <div class="grow" />
      </main>
    );
  }

  return (
    <main class="shell pad stack" style="gap:1.25rem">
      <span class="eyebrow">One last question</span>
      <h1 style="font-size:var(--step-2)">Which class are you in?</h1>
      <p class="note">
        GuruJi teaches only from your class's NCERT books — that's why this one matters.
      </p>

      <div class="grades" role="group" aria-label="Choose your class">
        {GRADES.map((g) => (
          <button
            key={g}
            type="button"
            aria-pressed={grade === g}
            onClick={() => setGrade(g)}
          >
            <b>{g}</b>
            <small>Class</small>
          </button>
        ))}
      </div>

      <div class="field">
        <label id="board-label">Board</label>
        <div
          role="group"
          aria-labelledby="board-label"
          style="display:flex;gap:4px;padding:4px;border:1px solid var(--line-2);border-radius:var(--r-md);background:var(--surface)"
        >
          <span style="flex:1;text-align:center;padding:.5rem 0;border-radius:var(--r-sm);background:var(--accent-fill);color:var(--accent-lit);font-weight:500;font-size:.875rem">
            NCERT
          </span>
          <span style="flex:1;display:flex;align-items:center;justify-content:center;gap:.4rem;padding:.5rem 0;color:var(--dim);font-size:.875rem">
            State board
            <em class="mono" style="font-style:normal;border:1px solid var(--line-2);padding:2px 5px;border-radius:3px">
              SOON
            </em>
          </span>
        </div>
      </div>

      {error ? <Banner tone="fault">{error}</Banner> : null}

      <div class="grow" />
      <button
        class="btn"
        data-tone="primary"
        data-full
        disabled={!grade || busy}
        onClick={() => void begin()}
      >
        {busy ? "Setting up…" : "Let's get started"}
      </button>
    </main>
  );
}
