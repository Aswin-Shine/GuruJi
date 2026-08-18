import type { ComponentChildren, JSX } from "preact";
import { navigate, type Path } from "../router";

/** The GuruJi mark. Devanagari, so the product is recognisable to a Class 5
 *  student before any English is read. aria-hidden because the accessible name
 *  lives on the heading beside it, not on decoration. */
export function Seal({ small = false }: { small?: boolean }): JSX.Element {
  return (
    <div class="seal" data-size={small ? "sm" : undefined} aria-hidden="true">
      गु
    </div>
  );
}

export function Banner({
  tone,
  children,
}: {
  tone?: "hold" | "fault";
  children: ComponentChildren;
}): JSX.Element {
  return (
    <div
      class="banner"
      data-tone={tone}
      // assertive only for real faults; a soft "slow down" should not interrupt
      // a screen reader mid-sentence.
      role={tone === "fault" ? "alert" : "status"}
    >
      {children}
    </div>
  );
}

export function BackLink({ to, label }: { to: Path; label: string }): JSX.Element {
  return (
    <button class="btn" style="min-height:2.25rem;padding:0 .75rem" onClick={() => navigate(to)}>
      ‹ <span>{label}</span>
    </button>
  );
}

/** Indian mobile entry. The +91 is fixed furniture, not a field: every pilot
 *  number is Indian, and a country-code dropdown is a decision the child does
 *  not need to make. Ten digits, numeric keypad, tabular figures. */
export function PhoneField({
  value,
  onInput,
  invalid,
  hint,
}: {
  value: string;
  onInput: (v: string) => void;
  invalid?: boolean;
  hint?: string;
}): JSX.Element {
  return (
    <div class="field">
      <label for="phone">Phone number</label>
      <div class="phone">
        <span aria-hidden="true">+91</span>
        <input
          id="phone"
          name="phone"
          type="tel"
          inputMode="numeric"
          autocomplete="tel-national"
          maxLength={10}
          placeholder="98765 43210"
          value={value}
          aria-invalid={invalid ? "true" : undefined}
          aria-describedby={hint ? "phone-hint" : undefined}
          onInput={(e) =>
            onInput((e.currentTarget as HTMLInputElement).value.replace(/\D/g, "").slice(0, 10))
          }
        />
      </div>
      {hint ? (
        <p class="hint" id="phone-hint" data-tone={invalid ? "fault" : undefined}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function Spinner({ label }: { label: string }): JSX.Element {
  return (
    <p class="note" role="status">
      {label}
    </p>
  );
}
