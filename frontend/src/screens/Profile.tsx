import type { JSX } from "preact";
import { useContext, useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { AVATARS } from "../backend";
import { MenuContext, MenuIcon, ThemeToggle } from "../components/Shell";
import { Banner } from "../components/ui";

import { navigate } from "../router";
import { session } from "../session";
import { theme, type Theme } from "../theme";

/**
 * The student's own page.
 *
 * Two deliberate absences, both worth stating rather than quietly working around:
 *
 * NO PHOTO UPLOAD. The brief asked for one. There is no S3 bucket at Phase 1, no
 * image moderation of any kind, and no verifiable parental consent flow — which
 * means storing a photograph of a minor has no lawful basis under the DPDP Rules
 * and no safety review behind it. A preset glyph gives a 12-year-old the same
 * "this is mine" feeling, renders offline, costs nothing, and cannot be a
 * safeguarding incident. Revisit when consent and moderation exist, not before.
 *
 * NO EMAIL / ADDRESS / SCHOOL FIELDS. Every extra field about a child is data to
 * protect, disclose, and eventually delete. The product needs a name to greet
 * them by and a class to teach at. Nothing here needs the rest.
 */

const CLASSES = [5, 6, 7, 8, 9, 10] as const;

const LANGS = [
  { key: "hinglish", label: "Hinglish" },
  { key: "english", label: "English" },
  { key: "hindi", label: "हिंदी" },
] as const;

const THEMES: { key: Theme; label: string }[] = [
  { key: "light", label: "Light" },
  { key: "dark", label: "Dark" },
  { key: "system", label: "Auto" },
];

export function Profile(): JSX.Element {
  const openMenu = useContext(MenuContext);
  const s = session.get();

  const [name, setName] = useState(s?.displayName ?? "");
  const [avatar, setAvatar] = useState(s?.avatar ?? AVATARS[0]);
  const [grade, setGrade] = useState<number | null>(null);
  const [lang, setLang] = useState("hinglish");
  const [themeSel, setThemeSel] = useState<Theme>(theme.get());
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /* Resolved from /students/me rather than trusted from the session, which is
     empty on any device that did not go through onboarding. */
  const [sid, setSid] = useState<string | null>(s?.studentId ?? null);

  /* Parent invite. Restored — I dropped it when Account.tsx was replaced by this
     screen, which silently removed the parent dashboard the Founder Discovery
     Workbook names as the answer to "why not just use ChatGPT". */
  const [parentPhone, setParentPhone] = useState("");
  const [pin, setPin] = useState<string | null>(null);
  const [linking, setLinking] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        // /me resolves from the token, so this works on a device that only ever
        // signed in — the case that produced the old error banner.
        const p = await api.me();
        setSid(p.id);
        session.rememberStudentId(p.id);
        session.rememberProfile({ grade: p.grade });
        setGrade(p.grade);
        setLang(p.preferred_language || "hinglish");
        if (p.display_name) setName(p.display_name);
        if (p.avatar) setAvatar(p.avatar);
      } catch {
        setError("Couldn't load your profile. Check your connection and reload.");
      }
    })();
  }, []);

  async function save(): Promise<void> {
    if (!sid) {
      setError("Still loading your profile — try again in a moment.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const p = await api.updateProfile(sid, {
        display_name: name.trim(),
        avatar,
        preferred_language: lang,
        ...(grade ? { grade } : {}),
      });
      session.rememberProfile({ displayName: p.display_name, avatar: p.avatar, grade: p.grade });
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2400);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save.");
    } finally {
      setBusy(false);
    }
  }

  async function invite(): Promise<void> {
    if (!sid || parentPhone.length !== 10) return;
    setLinking(true);
    setError(null);
    try {
      const r = await api.linkParent(sid, `+91${parentPhone}`);
      setPin(r.link_pin);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't create the invite.");
    } finally {
      setLinking(false);
    }
  }

  const inviteUrl = pin && sid ? `${location.origin}/parent#s=${sid}&pin=${pin}` : null;

  return (
    <>
      <header class="topbar">
        <button class="icon-btn only-mobile" onClick={openMenu} aria-label="Open menu">
          <MenuIcon />
        </button>
        <div class="grow">
          <h1>Your profile</h1>
          <p>Only you and your parent can see this</p>
        </div>
        <div class="only-mobile">
          <ThemeToggle />
        </div>
      </header>

      <div class="scroll">
        <div class="pane stack screen-in" style="padding-bottom:var(--gap-5)">
          <div class="profile-head">
            <span class="avatar" data-size="lg" aria-hidden="true">
              {avatar}
            </span>
            <b>{name.trim() || "Add your name"}</b>
            <span>{grade ? `Class ${grade} · NCERT` : "NCERT"}</span>
          </div>

          {error ? <Banner tone="fault">{error}</Banner> : null}
          {saved ? <Banner>Saved.</Banner> : null}

          <section class="panel">
            <span class="eyebrow">What should GuruJi call you?</span>
            <div class="field">
              <label for="name">Your name</label>
              <input
                id="name"
                value={name}
                maxLength={24}
                placeholder="Aarav"
                autocomplete="given-name"
                onInput={(e: Event) => setName((e.currentTarget as HTMLInputElement).value)}
              />
            </div>
            <p class="note">
              First name is plenty. Don't put your full name, school, or address here.
            </p>
          </section>

          <section class="panel">
            <span class="eyebrow">Pick your look</span>
            <div class="glyphs" role="group" aria-label="Choose an avatar">
              {AVATARS.map((g) => (
                <button
                  class="glyph"
                  key={g}
                  aria-pressed={g === avatar}
                  aria-label={`Avatar ${g}`}
                  onClick={() => setAvatar(g)}
                >
                  {g}
                </button>
              ))}
            </div>
            <p class="note">
              Photos aren't available — GuruJi doesn't store pictures of students.
            </p>
          </section>

          <section class="panel">
            <span class="eyebrow">Which class are you in?</span>
            <div class="seg" role="group" aria-label="Your class">
              {CLASSES.map((c) => (
                <button key={c} aria-pressed={grade === c} onClick={() => setGrade(c)}>
                  {c}
                </button>
              ))}
            </div>
            <p class="note">
              GuruJi only teaches from your class's textbook, so this changes the answers you
              get. Sharing a phone with a brother or sister? Change it before you start.
            </p>
          </section>

          <section class="panel">
            <span class="eyebrow">Answer language</span>
            <div class="seg" role="group" aria-label="Answer language">
              {LANGS.map((l) => (
                <button key={l.key} aria-pressed={lang === l.key} onClick={() => setLang(l.key)}>
                  {l.label}
                </button>
              ))}
            </div>
          </section>

          <section class="panel">
            <span class="eyebrow">Theme</span>
            <div class="seg" role="group" aria-label="Theme">
              {THEMES.map((t) => (
                <button
                  key={t.key}
                  aria-pressed={themeSel === t.key}
                  onClick={() => {
                    setThemeSel(t.key);
                    theme.set(t.key);
                  }}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <p class="note">Auto follows your phone. Saved on this device only.</p>
          </section>

          <button
            class="btn"
            data-tone="primary"
            style="width:100%;margin-block:var(--gap-3) var(--gap-4)"
            disabled={busy}
            onClick={() => void save()}
          >
            {busy ? "Saving…" : "Save"}
          </button>

          <section class="panel">
            <span class="eyebrow">Link a parent</span>
            <p class="note">
              Your parent gets a page showing which topics you're working on — and anything
              GuruJi blocked. They never see your ordinary conversations.
            </p>

            {pin === null ? (
              <>
                <div class="field" style="margin-top:var(--gap-2)">
                  <label for="pp">Parent's phone number</label>
                  <div class="phone">
                    <span>+91</span>
                    <input
                      id="pp"
                      inputMode="numeric"
                      autocomplete="tel-national"
                      placeholder="98765 43210"
                      maxLength={10}
                      value={parentPhone}
                      onInput={(e: Event) =>
                        setParentPhone(
                          (e.currentTarget as HTMLInputElement).value.replace(/\D/g, "").slice(0, 10),
                        )
                      }
                    />
                  </div>
                </div>
                <button
                  class="btn"
                  data-tone="primary"
                  data-full
                  style="margin-top:var(--gap-2)"
                  disabled={parentPhone.length !== 10 || linking || !sid}
                  onClick={() => void invite()}
                >
                  {linking ? "Creating…" : "Create invite"}
                </button>
              </>
            ) : (
              <>
                <p class="note" style="margin-top:var(--gap-2)">
                  Send this link to your parent yourself — GuruJi doesn't message them. After 5
                  wrong PIN attempts the invite stops working and you'll need a new one.
                </p>
                <div class="field">
                  <label for="inv">Invite link</label>
                  <input id="inv" readOnly value={inviteUrl ?? ""} onFocus={(e: Event) => (e.currentTarget as HTMLInputElement).select()} />
                </div>
                <div class="field">
                  <label for="pin">PIN</label>
                  <input id="pin" readOnly class="mono" value={pin} />
                </div>
                <button class="btn" data-full onClick={() => setPin(null)}>
                  Invite a different number
                </button>
              </>
            )}
          </section>

          <button
            class="btn"
            style="width:100%;margin-top:var(--gap-2)"
            onClick={() => {
              session.clear();
              navigate("/", true);
            }}
          >
            Sign out
          </button>
        </div>
      </div>
    </>
  );
}
