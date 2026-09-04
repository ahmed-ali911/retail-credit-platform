// Appearance — a *personal, browser-local* override of the 4 core colour tokens.
// NOT synced to the backend, NOT shared across users or devices. It is applied
// by setting CSS custom properties on :root at runtime. It is deliberately
// separate from the Step 6 maker-checker config system, which is for
// financial/policy values.

const STORAGE_KEY = "rc.appearance";

export const APPEARANCE_TOKENS = [
  { key: "primary", cssVar: "--color-primary", label: "Primary" },
  { key: "secondary", cssVar: "--color-secondary", label: "Success / active" },
  { key: "warm", cssVar: "--color-warm", label: "Attention" },
  { key: "danger", cssVar: "--color-danger", label: "Overdue / error" },
] as const;

export type AppearanceKey = (typeof APPEARANCE_TOKENS)[number]["key"];
export type Appearance = Record<AppearanceKey, string>;

// must match the defaults in src/styles/tokens.css
export const DEFAULT_APPEARANCE: Appearance = {
  primary: "#2c5fd6",
  secondary: "#219653",
  warm: "#9c7b4f",
  danger: "#c0392b",
};

export function loadAppearance(): Appearance {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_APPEARANCE };
    const parsed = JSON.parse(raw) as Partial<Appearance>;
    return { ...DEFAULT_APPEARANCE, ...parsed };
  } catch {
    return { ...DEFAULT_APPEARANCE };
  }
}

export function saveAppearance(a: Appearance): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(a));
  } catch {
    /* ignore storage errors */
  }
}

export function clearAppearance(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

/** Apply (or, with DEFAULT_APPEARANCE, effectively reset) the tokens on :root. */
export function applyAppearance(a: Appearance): void {
  const root = document.documentElement;
  for (const { key, cssVar } of APPEARANCE_TOKENS) {
    const value = a[key] || DEFAULT_APPEARANCE[key];
    root.style.setProperty(cssVar, value);
  }
}

/** Call once on app boot. */
export function initAppearance(): void {
  applyAppearance(loadAppearance());
}
