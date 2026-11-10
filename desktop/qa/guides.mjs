// Every first-visit intro, tour and tip the app can show (`profile.seen_intros` keys). A QA book
// marks them seen before the app loads: which guide claims a visit, and when, depends on timing
// these scripts don't control (Lucius's reply failing mid-import, say), and an open guide covers
// the controls they click. The guides have their own tests under frontend/src/components/intro.
export const SEEN_GUIDES = [
  // Screen intros (frontend/src/components/intro/screenIntros.tsx, assets/assetsIntro.tsx)
  "accounts",
  "assets",
  "dashboard",
  "investments",
  "intel",
  "commitments",
  "automations",
  "skills",
  "connectors",
  "plan",
  "plan-cashflow",
  "plan-outlook",
  "plan-whatif-ask",
  // Lucius's tours and tips (frontend/src/components/intro/LuciusTours.tsx)
  "lucius",
  "lucius-screen",
  "lucius-drag",
  "lucius-restore",
  "lucius-attach",
];
