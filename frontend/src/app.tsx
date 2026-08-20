import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { Shell } from "./components/Shell";
import { conversationParam, navigate, useRoute } from "./router";
import { session } from "./session";
import { Auth } from "./screens/Auth";
import { Chat, clearPhotoPreviews } from "./screens/Chat";
import { Onboarding } from "./screens/Onboarding";
import { Parent } from "./screens/Parent";
import { Profile } from "./screens/Profile";

/** Re-render whenever the session changes — including when api.ts clears it on
 *  a 401, which is what makes an expired token bounce every screen to sign-in
 *  without each screen having to check. */
/* Photo previews are blobs held in memory for the tab. Sign-out is the moment
   they must go: the pictures belong to the student who just left, and nothing
   about them was ever stored server-side to come back to. Wired here rather than
   inside session.ts so the session module keeps knowing nothing about the chat
   screen. */
session.subscribe(() => {
  if (!session.token()) clearPhotoPreviews();
});

function useSession() {
  const [, tick] = useState(0);
  useEffect(() => session.subscribe(() => tick((n) => n + 1)), []);
  return session.get();
}

/** Build marker, written to the DOM rather than merely exported — Vite tree-shakes
 *  an exported const that nothing imports, which would leave a check that always
 *  reports "not deployed". A marker you cannot trust is worse than no marker.
 *
 *  Verify a deploy:
 *    docker compose exec web grep -c "$BUILD" /usr/share/nginx/html/assets/index.*.js
 *  Or in the browser console: document.documentElement.dataset.build
 */
const BUILD = "guruji-web-1";
document.documentElement.dataset.build = BUILD;

export function App(): JSX.Element {
  const route = useRoute();
  const s = useSession();

  useEffect(() => {
    if (!s && route !== "/" && route !== "/verify") {
      navigate("/", true);
      return;
    }
    // A parent has no student profile and no chat. Keep them out of screens that
    // would only ever 403 for them.
    if (s?.role === "parent" && route !== "/parent") navigate("/parent", true);
  }, [s, route]);

  // Auth, onboarding and the parent portal are single-purpose full-page flows.
  // Wrapping them in the chat shell would put a "New chat" sidebar next to a
  // sign-in form, which is noise at the exact moment the screen should be one
  // decision wide.
  if (!s) return <Shell bare><Auth /></Shell>;
  if (s.role === "parent") return <Shell bare><Parent /></Shell>;
  // Onboarding owns /start outright: it is the signup route and nothing else.
  // Auth only sends a student here when the account genuinely has no profile
  // (see Auth.verify), and "New chat" goes to /chat — so whether onboarding shows
  // depends on the account, never on what this browser happens to have cached.
  if (route === "/start") return <Shell bare><Onboarding /></Shell>;

  const screen =
    route === "/account" ? <Profile /> :
      /* /history is folded into the sidebar. The old standalone History screen was
         a dead end: it rendered a transcript with no composer, so a student could
         read a past chat and had no way to continue it. Same content, now with the
         conversation open in the chat surface where they can type. */
      conversationParam() || route === "/chat" || route === "/history" ? <Chat /> :
        <Chat />;

  return <Shell>{screen}</Shell>;
}
