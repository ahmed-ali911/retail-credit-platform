import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { AppearancePanel } from "../components/AppearancePanel";
import {
  APPEARANCE_TOKENS,
  DEFAULT_APPEARANCE,
  PRESETS,
  adjustColor,
  detectPreset,
} from "../lib/appearance";

function rootVar(name: string) {
  return document.documentElement.style.getPropertyValue(name).trim();
}
function pickerValue(key: string) {
  return (screen.getByTestId(`appearance-input-${key}`) as HTMLInputElement).value;
}
function stored() {
  const raw = localStorage.getItem("rc.appearance");
  return raw ? JSON.parse(raw) : null;
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("style");
});

// --------------------------------------------------------------------------- //
// A — preset gallery
// --------------------------------------------------------------------------- //
describe("Step 17 — preset gallery", () => {
  it("ships 6 presets, all built from the platform colour family (no arbitrary hues)", () => {
    expect(PRESETS.length).toBe(6);
    expect(PRESETS[0].id).toBe("default");
    // default preset === the shipped palette exactly
    expect(PRESETS[0].colors).toEqual(DEFAULT_APPEARANCE);

    const hue = (hex: string) => {
      const n = parseInt(hex.slice(1), 16);
      const r = ((n >> 16) & 255) / 255;
      const g = ((n >> 8) & 255) / 255;
      const b = (n & 255) / 255;
      const max = Math.max(r, g, b);
      const min = Math.min(r, g, b);
      if (max === min) return 0;
      const d = max - min;
      let h: number;
      if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      return (h * 60 + 360) % 360;
    };

    for (const p of PRESETS) {
      // primary stays a navy / royal blue
      expect(hue(p.colors.primary)).toBeGreaterThan(200);
      expect(hue(p.colors.primary)).toBeLessThan(245);
      // success stays green / teal-green (default is ~146°)
      expect(hue(p.colors.secondary)).toBeGreaterThan(120);
      expect(hue(p.colors.secondary)).toBeLessThan(185);
      // attention stays a warm gold / brown
      expect(hue(p.colors.warm)).toBeGreaterThan(20);
      expect(hue(p.colors.warm)).toBeLessThan(50);
      // danger stays a brick red
      const dh = hue(p.colors.danger);
      expect(dh < 20 || dh > 350).toBe(true);
    }
  });

  it("selecting a preset updates the live preview and all 6 custom colours", async () => {
    render(<AppearancePanel />);
    const user = userEvent.setup();

    const navy = PRESETS.find((p) => p.id === "deep-navy")!;
    await user.click(screen.getByTestId("preset-deep-navy"));

    // all 6 pickers now match the preset
    for (const t of APPEARANCE_TOKENS) {
      expect(pickerValue(t.key)).toBe(navy.colors[t.key]);
    }
    // live preview (:root vars) updated — brightness/accent at 100 => exact hex
    expect(rootVar("--color-primary")).toBe(navy.colors.primary);
    expect(rootVar("--color-bg")).toBe(navy.colors.background);

    // selection indicator
    expect(screen.getByTestId("preset-deep-navy")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("preset-active")).toHaveTextContent("Deep Navy");
  });

  it("hand-editing a colour after a preset flips the indicator to 'Custom' (no false selection)", async () => {
    render(<AppearancePanel />);
    const user = userEvent.setup();

    await user.click(screen.getByTestId("preset-teal-forward"));
    expect(screen.getByTestId("preset-teal-forward")).toHaveAttribute("aria-checked", "true");

    fireEvent.change(screen.getByTestId("appearance-input-primary"), {
      target: { value: "#101820" },
    });

    expect(screen.getByTestId("preset-active")).toHaveTextContent("Custom");
    for (const p of PRESETS) {
      expect(screen.getByTestId(`preset-${p.id}`)).toHaveAttribute("aria-checked", "false");
    }
    expect(detectPreset({ ...DEFAULT_APPEARANCE, primary: "#101820" })).toBe("custom");
  });
});

// --------------------------------------------------------------------------- //
// B/C/D — extra pickers + computed sliders
// --------------------------------------------------------------------------- //
describe("Step 17 — Background/Surface pickers + Brightness/Accent sliders", () => {
  it("exposes 6 colour pickers including Background and Card/Surface", () => {
    render(<AppearancePanel />);
    expect(screen.getByTestId("appearance-input-background")).toBeInTheDocument();
    expect(screen.getByTestId("appearance-input-surface")).toBeInTheDocument();
    expect(pickerValue("background")).toBe(DEFAULT_APPEARANCE.background);
    expect(pickerValue("surface")).toBe(DEFAULT_APPEARANCE.surface);
  });

  it("Brightness changes the computed preview without touching the stored base colours", () => {
    render(<AppearancePanel />);

    const baseComputed = rootVar("--color-primary");
    fireEvent.change(screen.getByTestId("appearance-brightness"), {
      target: { value: "125" },
    });

    // computed :root value changed…
    expect(rootVar("--color-primary")).not.toBe(baseComputed || DEFAULT_APPEARANCE.primary);
    expect(screen.getByTestId("computed-primary")).not.toHaveTextContent(
      DEFAULT_APPEARANCE.primary,
    );
    // …but the six base values did NOT
    expect(pickerValue("primary")).toBe(DEFAULT_APPEARANCE.primary);
    expect(stored().primary).toBe(DEFAULT_APPEARANCE.primary);
    expect(stored().brightness).toBe(125);
  });

  it("Accent intensity adjusts the 4 accent colours only, leaving Background/Surface alone", () => {
    render(<AppearancePanel />);

    fireEvent.change(screen.getByTestId("appearance-accent-intensity"), {
      target: { value: "70" },
    });

    // an accent colour is re-computed
    expect(screen.getByTestId("computed-secondary")).not.toHaveTextContent(
      DEFAULT_APPEARANCE.secondary,
    );
    // background / surface are untouched by accent intensity (brightness still 100)
    expect(screen.getByTestId("computed-background")).toHaveTextContent(
      DEFAULT_APPEARANCE.background,
    );
    expect(screen.getByTestId("computed-surface")).toHaveTextContent(
      DEFAULT_APPEARANCE.surface,
    );
    // base values unchanged
    expect(pickerValue("secondary")).toBe(DEFAULT_APPEARANCE.secondary);
    expect(stored().accentIntensity).toBe(70);
  });

  it("adjustColor is an exact identity at 100% / 100%", () => {
    for (const t of APPEARANCE_TOKENS) {
      expect(adjustColor(DEFAULT_APPEARANCE[t.key], 1, 1)).toBe(DEFAULT_APPEARANCE[t.key]);
    }
  });
});

// --------------------------------------------------------------------------- //
// persistence + reset
// --------------------------------------------------------------------------- //
describe("Step 17 — persistence & reset", () => {
  it("a preset + both slider positions survive a reload (browser-local)", async () => {
    const { unmount } = render(<AppearancePanel />);
    const user = userEvent.setup();

    await user.click(screen.getByTestId("preset-graphite"));
    fireEvent.change(screen.getByTestId("appearance-brightness"), { target: { value: "115" } });
    fireEvent.change(screen.getByTestId("appearance-accent-intensity"), { target: { value: "85" } });

    unmount();
    render(<AppearancePanel />);

    const graphite = PRESETS.find((p) => p.id === "graphite")!;
    expect(pickerValue("primary")).toBe(graphite.colors.primary);
    expect(pickerValue("background")).toBe(graphite.colors.background);
    expect(screen.getByTestId("brightness-value")).toHaveTextContent("115%");
    expect(screen.getByTestId("accent-intensity-value")).toHaveTextContent("85%");
    expect(screen.getByTestId("preset-active")).toHaveTextContent("Graphite");
  });

  it("Reset to defaults restores the Default preset AND both sliders to 100%", async () => {
    render(<AppearancePanel />);
    const user = userEvent.setup();

    await user.click(screen.getByTestId("preset-warm-gold"));
    fireEvent.change(screen.getByTestId("appearance-brightness"), { target: { value: "80" } });
    fireEvent.change(screen.getByTestId("appearance-accent-intensity"), { target: { value: "130" } });

    await user.click(screen.getByTestId("appearance-reset"));

    for (const t of APPEARANCE_TOKENS) {
      expect(pickerValue(t.key)).toBe(DEFAULT_APPEARANCE[t.key]);
    }
    expect(screen.getByTestId("brightness-value")).toHaveTextContent("100%");
    expect(screen.getByTestId("accent-intensity-value")).toHaveTextContent("100%");
    expect(screen.getByTestId("preset-default")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("preset-active")).toHaveTextContent("Default");
    expect(localStorage.getItem("rc.appearance")).toBeNull();
    expect(rootVar("--color-primary")).toBe(DEFAULT_APPEARANCE.primary);
    expect(rootVar("--color-secondary")).toBe(DEFAULT_APPEARANCE.secondary);
  });
});
