// Appearance — a *personal, browser-local* override of the core colour tokens.
// NOT synced to the backend, NOT shared across users or devices. It is applied
// by setting CSS custom properties on :root at runtime. It is deliberately
// separate from the Step 6 maker-checker config system, which is for
// financial/policy values.
//
// Step 17 extends it: 6 custom colours (was 4 — adds Background + Card/Surface),
// a preset gallery (shortcuts that set those 6 values), and two computed
// adjustment sliders — Brightness (lightness of the whole palette) and Accent
// Intensity (saturation of the 4 accent colours only). Sliders are a derived
// layer on top of the 6 base colours; they never rewrite the stored values.

const STORAGE_KEY = "rc.appearance";

// The 4 accent colours — Accent Intensity targets these; Background/Surface are
// left alone by it.
export const ACCENT_TOKENS = [
  { key: "primary", cssVar: "--color-primary", label: "Primary" },
  { key: "secondary", cssVar: "--color-secondary", label: "Success / active" },
  { key: "warm", cssVar: "--color-warm", label: "Attention" },
  { key: "danger", cssVar: "--color-danger", label: "Overdue / error" },
] as const;

const SURFACE_TOKENS = [
  { key: "background", cssVar: "--color-bg", label: "Background" },
  { key: "surface", cssVar: "--color-surface", label: "Card / surface" },
] as const;

export const APPEARANCE_TOKENS = [...ACCENT_TOKENS, ...SURFACE_TOKENS] as const;

export type AppearanceKey = (typeof APPEARANCE_TOKENS)[number]["key"];
export type Appearance = Record<AppearanceKey, string>;

const ACCENT_KEYS = ACCENT_TOKENS.map((t) => t.key) as AppearanceKey[];

// must match the defaults in src/styles/tokens.css
export const DEFAULT_APPEARANCE: Appearance = {
  primary: "#2c5fd6",
  secondary: "#219653",
  warm: "#9c7b4f",
  danger: "#c0392b",
  background: "#f7fbfd",
  surface: "#ffffff",
};

export const BRIGHTNESS_RANGE = { min: 70, max: 130, default: 100 } as const;
export const ACCENT_INTENSITY_RANGE = { min: 60, max: 140, default: 100 } as const;

export interface AppearanceSettings {
  colors: Appearance;
  brightness: number; // percent, 70–130
  accentIntensity: number; // percent, 60–140
}

export const DEFAULT_SETTINGS: AppearanceSettings = {
  colors: { ...DEFAULT_APPEARANCE },
  brightness: BRIGHTNESS_RANGE.default,
  accentIntensity: ACCENT_INTENSITY_RANGE.default,
};

// --------------------------------------------------------------------------- //
// Preset gallery
//
// Every preset is built from THIS platform's established colour family — navy /
// royal-blue primary, teal/green success, warm gold/brown attention, brick-red
// danger, near-white surfaces. Presets vary which of navy / teal / gold sits
// visually forward and how light or heavy the overall balance is. No preset
// introduces a hue outside that family, and none is bright, playful or
// saturated. A preset is only a shortcut that sets the 6 custom colours below —
// it is not a separate storage mechanism.
// --------------------------------------------------------------------------- //
export interface Preset {
  id: string;
  name: string;
  colors: Appearance;
}

export const PRESETS: Preset[] = [
  {
    id: "default",
    name: "Default",
    // the shipped palette
    colors: {
      primary: "#2c5fd6",
      secondary: "#219653",
      warm: "#9c7b4f",
      danger: "#c0392b",
      background: "#f7fbfd",
      surface: "#ffffff",
    },
  },
  {
    id: "deep-navy",
    name: "Deep Navy",
    // navy-forward, primary pulled toward --color-primary-dark, cooler ground
    colors: {
      primary: "#1f3a8a",
      secondary: "#1f7a49",
      warm: "#8a6c45",
      danger: "#b23b2e",
      background: "#eef2f8",
      surface: "#ffffff",
    },
  },
  {
    id: "teal-forward",
    name: "Teal Forward",
    // success/secondary leans teal-green and sits forward; primary cools
    colors: {
      primary: "#2b6cb0",
      secondary: "#1c8b7a",
      warm: "#977a4e",
      danger: "#bd4133",
      background: "#f2f9f9",
      surface: "#ffffff",
    },
  },
  {
    id: "warm-gold",
    name: "Warm Gold",
    // gold/brown attention sits forward; primary muted so it recedes; warm ground
    colors: {
      primary: "#3a5a9c",
      secondary: "#3f8a54",
      warm: "#8a6a3c",
      danger: "#b8402f",
      background: "#faf7f1",
      surface: "#fffdf9",
    },
  },
  {
    id: "high-contrast",
    name: "High Contrast",
    // darker, deeper accents on a crisp cool-grey ground for maximum legibility
    colors: {
      primary: "#12327a",
      secondary: "#12703f",
      warm: "#7a5c30",
      danger: "#a5281c",
      background: "#eef1f7",
      surface: "#ffffff",
    },
  },
  {
    id: "graphite",
    name: "Graphite",
    // a heavier, dimmer light theme (not dark mode): grey ground + off-white
    // cards, accents lifted slightly so they still read on the darker surface
    colors: {
      primary: "#3f74e0",
      secondary: "#2aa564",
      warm: "#a98a5c",
      danger: "#d0493a",
      background: "#e7ebf1",
      surface: "#f4f6fa",
    },
  },
];

const norm = (hex: string) => hex.trim().toLowerCase();

function sameColors(a: Appearance, b: Appearance): boolean {
  return APPEARANCE_TOKENS.every((t) => norm(a[t.key]) === norm(b[t.key]));
}

/** The id of the preset whose palette exactly matches `colors`, or "custom". */
export function detectPreset(colors: Appearance): string {
  const hit = PRESETS.find((p) => sameColors(p.colors, colors));
  return hit ? hit.id : "custom";
}

// --------------------------------------------------------------------------- //
// storage
// --------------------------------------------------------------------------- //
function clampRange(n: number, r: { min: number; max: number; default: number }) {
  if (!Number.isFinite(n)) return r.default;
  return Math.min(r.max, Math.max(r.min, Math.round(n)));
}

export function loadSettings(): AppearanceSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { colors: { ...DEFAULT_APPEARANCE }, ...sliderDefaults() };
    const parsed = JSON.parse(raw) as Partial<Appearance> & {
      brightness?: number;
      accentIntensity?: number;
    };
    return {
      colors: { ...DEFAULT_APPEARANCE, ...pickColors(parsed) },
      brightness: clampRange(
        parsed.brightness ?? BRIGHTNESS_RANGE.default,
        BRIGHTNESS_RANGE,
      ),
      accentIntensity: clampRange(
        parsed.accentIntensity ?? ACCENT_INTENSITY_RANGE.default,
        ACCENT_INTENSITY_RANGE,
      ),
    };
  } catch {
    return { colors: { ...DEFAULT_APPEARANCE }, ...sliderDefaults() };
  }
}

function sliderDefaults() {
  return {
    brightness: BRIGHTNESS_RANGE.default,
    accentIntensity: ACCENT_INTENSITY_RANGE.default,
  };
}

function pickColors(obj: Partial<Appearance>): Partial<Appearance> {
  const out: Partial<Appearance> = {};
  for (const t of APPEARANCE_TOKENS) {
    if (typeof obj[t.key] === "string") out[t.key] = obj[t.key];
  }
  return out;
}

/** Back-compat: the colours only (used by callers that don't care about sliders). */
export function loadAppearance(): Appearance {
  return loadSettings().colors;
}

export function saveSettings(s: AppearanceSettings): void {
  try {
    // flat shape — the 6 colours at the top level (unchanged from Step 14), plus
    // the two slider values alongside them.
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        ...s.colors,
        brightness: s.brightness,
        accentIntensity: s.accentIntensity,
      }),
    );
  } catch {
    /* ignore storage errors */
  }
}

export function saveAppearance(a: Appearance): void {
  saveSettings({ ...loadSettings(), colors: a });
}

export function clearAppearance(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

// --------------------------------------------------------------------------- //
// computed adjustment (Brightness + Accent Intensity)
// --------------------------------------------------------------------------- //
function hexToRgb(hex: string): [number, number, number] {
  let h = hex.replace("#", "").trim();
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const n = parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgbToHex(r: number, g: number, b: number): string {
  const to = (v: number) =>
    Math.max(0, Math.min(255, Math.round(v)))
      .toString(16)
      .padStart(2, "0");
  return `#${to(r)}${to(g)}${to(b)}`;
}

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255;
  g /= 255;
  b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0;
  let s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case r:
        h = (g - b) / d + (g < b ? 6 : 0);
        break;
      case g:
        h = (b - r) / d + 2;
        break;
      default:
        h = (r - g) / d + 4;
    }
    h /= 6;
  }
  return [h, s, l];
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  if (s === 0) {
    const v = l * 255;
    return [v, v, v];
  }
  const hue2rgb = (p: number, q: number, t: number) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [
    hue2rgb(p, q, h + 1 / 3) * 255,
    hue2rgb(p, q, h) * 255,
    hue2rgb(p, q, h - 1 / 3) * 255,
  ];
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/**
 * Adjust `hex` by two independent multipliers:
 *   lightMul  — scales HSL lightness   (Brightness slider / 100)
 *   satMul    — scales HSL saturation  (Accent Intensity slider / 100)
 * At `1, 1` this is an exact identity — the input hex is returned untouched, so
 * the stored base colour is what lands on :root when both sliders are at 100%.
 */
export function adjustColor(hex: string, lightMul: number, satMul: number): string {
  if (lightMul === 1 && satMul === 1) return hex;
  const [r, g, b] = hexToRgb(hex);
  const [h, s, l] = rgbToHsl(r, g, b);
  const [nr, ng, nb] = hslToRgb(h, clamp01(s * satMul), clamp01(l * lightMul));
  return rgbToHex(nr, ng, nb);
}

/** The 6 CSS-var values that should be on :root for the given settings. */
export function computeApplied(s: AppearanceSettings): Record<string, string> {
  const lightMul = s.brightness / 100;
  const satMul = s.accentIntensity / 100;
  const out: Record<string, string> = {};
  for (const t of APPEARANCE_TOKENS) {
    const base = s.colors[t.key] || DEFAULT_APPEARANCE[t.key];
    const isAccent = ACCENT_KEYS.includes(t.key);
    out[t.cssVar] = adjustColor(base, lightMul, isAccent ? satMul : 1);
  }
  return out;
}

// --------------------------------------------------------------------------- //
// apply to :root
// --------------------------------------------------------------------------- //
/**
 * Apply the appearance to :root. `brightness` / `accentIntensity` default to
 * 100 so existing callers (`applyAppearance(colors)`) keep the exact same
 * behaviour they had in Step 14.
 */
export function applyAppearance(
  colors: Appearance,
  brightness: number = BRIGHTNESS_RANGE.default,
  accentIntensity: number = ACCENT_INTENSITY_RANGE.default,
): void {
  const root = document.documentElement;
  const applied = computeApplied({ colors, brightness, accentIntensity });
  for (const [cssVar, value] of Object.entries(applied)) {
    root.style.setProperty(cssVar, value);
  }
}

export function applySettings(s: AppearanceSettings): void {
  applyAppearance(s.colors, s.brightness, s.accentIntensity);
}

/** Call once on app boot. */
export function initAppearance(): void {
  applySettings(loadSettings());
}
