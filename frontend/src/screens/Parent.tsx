import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { ApiError, api } from "../api";
import type { FlaggedExchangeOut, StudentSummaryOut } from "../backend";
import { Banner, Seal, Spinner } from "../components/ui";
import { navigate, takeInvite } from "../router";
import { session } from "../session";

/**
 * The parent view. Summary only — the backend refuses to serve transcripts to a
 * parent by design, and this screen does not try to reconstruct them.
 *
 * Flagged exchanges ARE rendered. GET /v1/students/{id}/flagged exposes what
 * `moderation_flags` records, gated on the same role-AND-verified-link check as
 * /summary. This is the one place a parent
 * sees verbatim child-written text, and it is the deliberate exception to the
 * transcripts-stay-private rule below — a paraphrase would be useless for the
 * judgement a parent actually has to make.
 *
 * Still NOT rendered, because no endpoint supplies it: per-topic question counts.
 * `summary_for_parent()` returns topic names only; the counts in the design
 * reference have no source. Backend ask 5.
 */
export function Parent(): JSX.Element {
  const s = session.get();
  const [studentId, setStudentId] = useState<string | undefined>(s?.studentId);
  const [summary, setSummary] = useState<StudentSummaryOut | null>(null);
  /* `undefined` means "not loaded yet" and `[]` means "loaded, none found". The
     distinction matters more here than anywhere else in this app: rendering
     "0 flagged" while the real number is unknown is the worst available error in
     a child-safety feature. */
  const [flags, setFlags] = useState<FlaggedExchangeOut[] | undefined>(undefined);
  const [state, setState] = useState<"idle" | "linking" | "loading" | "done">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const invite = takeInvite();

      if (invite) {
        setState("linking");
        try {
          await api.verifyParentLink(invite.studentId, invite.pin);
          session.rememberStudent(invite.studentId);
          setStudentId(invite.studentId);
          await loadSummary(invite.studentId);
          return;
        } catch (err) {
          setError(
            err instanceof ApiError && err.status === 403
              ? "That invite didn't work. Ask your child to send a fresh one — after 5 wrong tries the invite cancels itself."
              : err instanceof Error
                ? err.message
                : "That invite didn't work.",
          );
          setState("done");
          return;
        }
      }

      if (studentId) {
        await loadSummary(studentId);
        return;
      }
      setState("done");
    })();
    // Runs once: the invite is consumed from the URL on first read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadSummary(id: string): Promise<void> {
    setState("loading");
    try {
      setSummary(await api.summary(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the summary.");
    } finally {
      setState("done");
    }
    /* Separate request, separate failure. A flagged-exchange fetch that fails
       must not blank the whole summary, and the section says so rather than
       silently showing nothing — which would read as "no flags". */
    try {
      setFlags(await api.flagged(id));
    } catch {
      setFlags(undefined);
    }
  }

  async function signOut(): Promise<void> {
    try {
      await api.logout();
    } catch {
      /* local session clears either way */
    }
    session.clear();
    navigate("/", true);
  }

  if (state === "linking") {
    return (
      <main class="shell pad stack">
        <Seal />
        <h1 style="font-size:var(--step-2)">Linking you to your child…</h1>
        <Spinner label="One moment." />
      </main>
    );
  }

  return (
    <main class="portal">
      <header style="display:flex;align-items:center;gap:.75rem;flex-wrap:wrap">
        <Seal small />
        <div style="flex:1;min-width:0">
          <span class="eyebrow">Parent view</span>
          <h1 style="font-size:var(--step-2);margin-top:.25rem">
            {summary ? `Class ${summary.grade} · ${summary.board}` : "Your child's progress"}
          </h1>
        </div>
        <button class="btn" onClick={() => void signOut()}>
          Sign out
        </button>
      </header>

      {error ? <Banner tone="fault">{error}</Banner> : null}
      {state === "loading" ? <Spinner label="Loading the summary…" /> : null}

      {!summary && state === "done" && !error ? (
        <div class="panel" data-tone="quiet">
          <p class="lede">You're signed in, but no child is linked to this device yet.</p>
          <p class="note">
            Ask your child to open GuruJi, go to <strong>You → Link a parent</strong>, and send you
            the invite link. Opening that link on this device connects you.
          </p>
        </div>
      ) : null}

      {summary ? (
        <>
          <div class="tiles">
            <div class="tile">
              <b>{summary.total_messages}</b>
              <span>Questions asked, all time</span>
            </div>
            <div class="tile">
              <b>{summary.struggle_topics.length}</b>
              <span>Topics needing practice</span>
            </div>
            <div class="tile">
              <b>{summary.mastered_topics.length}</b>
              <span>Topics getting confident</span>
            </div>
            <div class="tile" data-tone={flags && flags.length > 0 ? "focus" : undefined}>
              <b>{flags === undefined ? "—" : flags.length}</b>
              <span>{flags === undefined ? "Blocked questions, unavailable" : "Questions GuruJi blocked"}</span>
            </div>
          </div>

          <div class="cols">
            <section class="panel">
              <span class="eyebrow">Needs practice</span>
              {summary.struggle_topics.length === 0 ? (
                <p class="note">
                  Nothing stands out yet. GuruJi updates this after each conversation.
                </p>
              ) : (
                <div class="topics">
                  {summary.struggle_topics.map((t) => (
                    <span class="topic" data-tone="focus" key={t}>
                      {t}
                    </span>
                  ))}
                </div>
              )}
            </section>

            <section class="panel">
              <span class="eyebrow" style="color:var(--muted)">
                Getting confident
              </span>
              {summary.mastered_topics.length === 0 ? (
                <p class="note">This fills in as your child works through more chapters.</p>
              ) : (
                <div class="topics">
                  {summary.mastered_topics.map((t) => (
                    <span class="topic" key={t}>
                      {t}
                    </span>
                  ))}
                </div>
              )}
            </section>
          </div>

          <section class="panel">
            <span class="eyebrow" style="color:var(--hold)">
              Blocked questions
            </span>
            {flags === undefined ? (
              <p class="note">
                This couldn't be loaded just now. Reload the page — GuruJi keeps every blocked
                exchange, so nothing is lost.
              </p>
            ) : flags.length === 0 ? (
              <p class="note">
                GuruJi hasn't blocked anything your child asked. If it ever does, the exact words
                appear here for you to read.
              </p>
            ) : (
              <div class="flags">
                {flags.map((f) => (
                  <article class="flag" key={f.id}>
                    <header>
                      <span data-dir={f.direction}>
                        {f.direction === "inbound" ? "Your child asked" : "GuruJi almost replied"}
                      </span>
                      <time datetime={f.flagged_at}>
                        {new Date(f.flagged_at).toLocaleDateString(undefined, {
                          day: "numeric",
                          month: "short",
                        })}
                      </time>
                    </header>
                    <p>{f.content}</p>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section class="panel" data-tone="quiet">
            <span class="eyebrow" style="color:var(--muted)">
              What you can and cannot see
            </span>
            <p class="note">
              You see this summary and anything GuruJi blocked — not the ordinary conversations.
              Your child's schoolwork questions stay theirs. That's a deliberate choice, not a
              missing feature.
            </p>
          </section>
        </>
      ) : null}
    </main>
  );
}
