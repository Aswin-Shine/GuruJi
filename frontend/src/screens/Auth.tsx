import type { JSX } from "preact";
import { useRef, useState } from "preact/hooks";
import { ApiError, api } from "../api";
import { OTP_LENGTH, type Role } from "../backend";
import { Banner, PhoneField, Seal } from "../components/ui";
import { navigate } from "../router";
import { session } from "../session";

type Step = "phone" | "code";

export function Auth(): JSX.Element {
  const [step, setStep] = useState<Step>("phone");
  const [who, setWho] = useState<Role>("student");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const codeRef = useRef<HTMLInputElement>(null);

  const e164 = `+91${phone}`;
  const phoneReady = phone.length === 10 && /^[6-9]/.test(phone);

  async function askForCode(): Promise<void> {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await api.requestOtp(e164);
      // dev_hint only exists while DEV_OTP_BYPASS is on. Surfacing it is
      // correct in that mode and impossible outside it — the backend refuses
      // to boot with the bypass on unless APP_ENV=local.
      if (r.dev_hint) setNotice(`Development mode — ${r.dev_hint}`);
      setStep("code");
      queueMicrotask(() => codeRef.current?.focus());
    } catch (err) {
      if (err instanceof ApiError && err.status === 501) {
        // Do not dress this up. Codes genuinely cannot be sent yet.
        setError(
          "Codes aren't being sent yet — GuruJi is in a closed pilot. Message us on WhatsApp to get added.",
        );
      } else {
        setError(err instanceof Error ? err.message : "That did not work.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function verify(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const t = await api.verifyOtp(e164, code, who);
      session.start(t);
      // Route on the role the SERVER returned, never the one requested.
      // get_or_create_user() hands back an existing account unchanged, so a
      // number already registered as a student stays a student even if the
      // "parent" toggle was pressed.
      if (t.role === "parent") {
        navigate("/parent", true);
        return;
      }

      // Onboarding is for accounts that genuinely have no profile yet, so ask the
      // server rather than guessing from this browser's cache. A returning student
      // on a new phone has an empty session and would otherwise be asked their
      // class again — and answering it a second time changes nothing, because
      // POST /v1/students 409s once a profile exists. The question would be pure
      // friction with a dead end behind it.
      try {
        const me = await api.me();
        session.rememberStudentId(me.id);
        session.rememberProfile({
          grade: me.grade,
          displayName: me.display_name,
          avatar: me.avatar,
        });
        navigate("/chat", true);
      } catch (err) {
        // 404 is the real "first-time student" signal and the only one that should
        // open onboarding. Any other failure (offline, 5xx) must NOT — sending a
        // registered student into signup on a flaky network is how a profile gets
        // second-guessed. Chat calls /students/me on mount and recovers there.
        navigate(err instanceof ApiError && err.status === 404 ? "/start" : "/chat", true);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "That code did not work.");
      setCode("");
      codeRef.current?.focus();
    } finally {
      setBusy(false);
    }
  }

  if (step === "code") {
    return (
      <main class="shell pad stack" style="gap:1.25rem">
        <button class="linkish" style="justify-self:start" onClick={() => setStep("phone")}>
          ‹ Use a different number
        </button>

        <h1 style="font-size:var(--step-3)">Enter the 6-digit code</h1>
        <p class="lede">
          Sent to +91 {phone.slice(0, 5)} {phone.slice(5)}
        </p>

        <div class="field">
          <label for="otp">Code</label>
          <input
            ref={codeRef}
            id="otp"
            name="one-time-code"
            type="text"
            inputMode="numeric"
            autocomplete="one-time-code"
            maxLength={OTP_LENGTH}
            placeholder="000000"
            style="font-family:var(--font-mono);font-size:1.5rem;letter-spacing:.5em;text-align:center"
            value={code}
            aria-invalid={error ? "true" : undefined}
            onInput={(e) =>
              setCode(
                (e.currentTarget as HTMLInputElement).value.replace(/\D/g, "").slice(0, OTP_LENGTH),
              )
            }
            onKeyDown={(e) => {
              if (e.key === "Enter" && code.length === OTP_LENGTH && !busy) void verify();
            }}
          />
        </div>

        {notice ? <Banner>{notice}</Banner> : null}
        {error ? (
          <Banner tone="fault">{error}</Banner>
        ) : null}

        <div class="grow" />
        <button
          class="btn"
          data-tone="primary"
          data-full
          disabled={code.length !== OTP_LENGTH || busy}
          onClick={() => void verify()}
        >
          {busy ? "Checking…" : "Verify"}
        </button>
      </main>
    );
  }

  return (
    <main class="shell pad stack" style="gap:1.25rem">
      <Seal />
      <h1 style="font-size:var(--step-3)">Namaste!</h1>
      <p class="lede">
        Main GuruJi hoon. I teach only from your NCERT textbook — if it isn't in there, I won't
        make it up.
      </p>

      <div class="grow" style="min-height:.5rem" />

      <PhoneField
        value={phone}
        onInput={setPhone}
        invalid={phone.length > 0 && !phoneReady}
        hint={
          phone.length > 0 && !phoneReady
            ? "Enter a 10-digit Indian mobile number."
            : "Pilot access is limited to invited numbers."
        }
      />

      <fieldset style="border:0;padding:0;margin:0" class="stack" role="radiogroup" aria-label="Who is signing in">
        <span class="eyebrow">I am the</span>
        <div style="display:flex;gap:4px;padding:4px;border:1px solid var(--line-2);border-radius:var(--r-md);background:var(--surface)">
          {(["student", "parent"] as const).map((r) => (
            <button
              key={r}
              type="button"
              role="radio"
              aria-checked={who === r}
              onClick={() => setWho(r)}
              style={`flex:1;min-height:2.5rem;border:0;border-radius:var(--r-sm);cursor:pointer;font-weight:500;background:${
                who === r ? "var(--accent-fill)" : "transparent"
              };color:${who === r ? "var(--accent-lit)" : "var(--muted)"}`}
            >
              {r === "student" ? "Student" : "Parent"}
            </button>
          ))}
        </div>
      </fieldset>

      {error ? <Banner tone="fault">{error}</Banner> : null}

      <div class="grow" />
      <button
        class="btn"
        data-tone="primary"
        data-full
        disabled={!phoneReady || busy}
        onClick={() => void askForCode()}
      >
        {busy ? "Sending…" : "Send code"}
      </button>
      <p class="note" style="text-align:center">
        Continuing means you agree to our terms. A parent should set this up with you.
      </p>
    </main>
  );
}
