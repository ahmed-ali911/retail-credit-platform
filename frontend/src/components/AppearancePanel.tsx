import { useMemo, useState } from "react";
import {
  ACCENT_INTENSITY_RANGE,
  APPEARANCE_TOKENS,
  BRIGHTNESS_RANGE,
  DEFAULT_SETTINGS,
  PRESETS,
  applySettings,
  clearAppearance,
  computeApplied,
  detectPreset,
  loadSettings,
  saveSettings,
  type AppearanceSettings,
  type Preset,
} from "../lib/appearance";
import { Card } from "./ui";
import { StatusBadge } from "./StatusBadge";

/**
 * Appearance (Step 14 Part E, extended in Step 17) — a *personal, browser-local*
 * cosmetic preference. NOT a business-rule config value: it does NOT go through
 * the Step 6 maker-checker approval flow, is NOT saved on the server, and is
 * NOT shared with other users or devices.
 *
 * Step 17 adds: a gallery of preset themes (each built only from this platform's
 * established navy / teal-green / gold / red colour family), two extra custom
 * colours (Background + Card/Surface), and two computed-adjustment sliders —
 * Brightness (palette lightness) and Accent Intensity (accent-colour
 * saturation). The sliders are a derived layer: they change what lands on
 * :root, never the 6 stored base colours.
 */
export function AppearancePanel() {
  const [settings, setSettings] = useState<AppearanceSettings>(() => loadSettings());

  const activePreset = detectPreset(settings.colors);
  const activePresetName =
    PRESETS.find((p) => p.id === activePreset)?.name ?? "Custom";
  const computed = useMemo(() => computeApplied(settings), [settings]);

  function commit(next: AppearanceSettings) {
    setSettings(next);
    saveSettings(next);
    applySettings(next);
  }

  const updateColor = (key: string, value: string) =>
    commit({ ...settings, colors: { ...settings.colors, [key]: value } });

  const selectPreset = (preset: Preset) =>
    commit({ ...settings, colors: { ...preset.colors } });

  const setBrightness = (n: number) => commit({ ...settings, brightness: n });
  const setAccentIntensity = (n: number) =>
    commit({ ...settings, accentIntensity: n });

  function reset() {
    clearAppearance();
    const fresh: AppearanceSettings = {
      colors: { ...DEFAULT_SETTINGS.colors },
      brightness: DEFAULT_SETTINGS.brightness,
      accentIntensity: DEFAULT_SETTINGS.accentIntensity,
    };
    setSettings(fresh);
    applySettings(fresh);
  }

  return (
    <div className="stack">
      <Card title="Appearance">
        <p className="muted" data-testid="appearance-local-note">
          This preference is saved <strong>on this browser only</strong>. It is
          not synced to the server or shared with other users or devices, and it
          does not go through the maker-checker approval used for business-rule
          parameters — it is a personal cosmetic setting.
        </p>

        {/* --- Preset gallery ------------------------------------------- */}
        <h3 style={{ marginTop: "1rem" }}>
          Theme presets{" "}
          <span className="muted" data-testid="preset-active">
            — {activePresetName}
          </span>
        </h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Each preset is built from this platform's own colour family (navy,
          teal-green, gold, red) at a different balance. Picking one sets the six
          custom colours below.
        </p>
        <div
          className="preset-gallery"
          role="radiogroup"
          aria-label="Theme presets"
        >
          {PRESETS.map((p) => {
            const selected = activePreset === p.id;
            return (
              <button
                key={p.id}
                type="button"
                role="radio"
                aria-checked={selected}
                data-testid={`preset-${p.id}`}
                className={`preset-swatch${selected ? " preset-swatch--selected" : ""}`}
                onClick={() => selectPreset(p)}
              >
                <span className="preset-stripes" aria-hidden>
                  <span style={{ background: p.colors.primary }} />
                  <span style={{ background: p.colors.secondary }} />
                  <span style={{ background: p.colors.warm }} />
                </span>
                <span className="preset-swatch__name">{p.name}</span>
              </button>
            );
          })}
        </div>

        {/* --- Custom colours (6) ------------------------------------- */}
        <h3 style={{ marginTop: "1.25rem" }}>Custom colours</h3>
        <div className="appearance-grid">
          {APPEARANCE_TOKENS.map((t) => (
            <label key={t.key} className="appearance-swatch">
              <span className="appearance-swatch__label">{t.label}</span>
              <span className="appearance-swatch__row">
                <input
                  type="color"
                  aria-label={t.label}
                  data-testid={`appearance-input-${t.key}`}
                  value={settings.colors[t.key]}
                  onChange={(e) => updateColor(t.key, e.target.value)}
                />
                <code>{settings.colors[t.key]}</code>
              </span>
            </label>
          ))}
        </div>

        {/* --- Brightness slider ------------------------------------- */}
        <div className="appearance-slider" data-testid="brightness-control">
          <div className="appearance-slider__head">
            <span className="appearance-swatch__label">Brightness</span>
            <span>
              <code data-testid="brightness-value">{settings.brightness}%</code>
              {settings.brightness !== BRIGHTNESS_RANGE.default && (
                <button
                  type="button"
                  className="btn-link"
                  data-testid="brightness-reset"
                  style={{ marginLeft: "0.5rem" }}
                  onClick={() => setBrightness(BRIGHTNESS_RANGE.default)}
                >
                  Reset
                </button>
              )}
            </span>
          </div>
          <input
            type="range"
            aria-label="Brightness"
            data-testid="appearance-brightness"
            min={BRIGHTNESS_RANGE.min}
            max={BRIGHTNESS_RANGE.max}
            step={1}
            value={settings.brightness}
            onChange={(e) => setBrightness(Number(e.target.value))}
          />
          <p className="muted appearance-slider__hint">
            Adjusts the lightness of the whole palette. A computed layer on top of
            the six colours above — it does not rewrite them.
          </p>
        </div>

        {/* --- Accent Intensity slider ----------------------------- */}
        <div className="appearance-slider" data-testid="accent-intensity-control">
          <div className="appearance-slider__head">
            <span className="appearance-swatch__label">Accent intensity</span>
            <span>
              <code data-testid="accent-intensity-value">
                {settings.accentIntensity}%
              </code>
              {settings.accentIntensity !== ACCENT_INTENSITY_RANGE.default && (
                <button
                  type="button"
                  className="btn-link"
                  data-testid="accent-intensity-reset"
                  style={{ marginLeft: "0.5rem" }}
                  onClick={() =>
                    setAccentIntensity(ACCENT_INTENSITY_RANGE.default)
                  }
                >
                  Reset
                </button>
              )}
            </span>
          </div>
          <input
            type="range"
            aria-label="Accent intensity"
            data-testid="appearance-accent-intensity"
            min={ACCENT_INTENSITY_RANGE.min}
            max={ACCENT_INTENSITY_RANGE.max}
            step={1}
            value={settings.accentIntensity}
            onChange={(e) => setAccentIntensity(Number(e.target.value))}
          />
          <p className="muted appearance-slider__hint">
            Adjusts the saturation of the four accent colours only
            (Primary / Success / Attention / Danger). Background and surface are
            left alone.
          </p>
        </div>

        <div className="inline-form" style={{ marginTop: "1rem" }}>
          <button
            type="button"
            className="btn-secondary"
            data-testid="appearance-reset"
            onClick={reset}
          >
            Reset to defaults
          </button>
        </div>
      </Card>

      <Card title="Live preview" soft>
        <div
          className="stack"
          data-testid="appearance-preview"
          style={{ gap: "0.6rem" }}
        >
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            <button className="btn-primary" type="button">
              Primary action
            </button>
            <button className="btn-secondary" type="button">
              Secondary
            </button>
          </div>
          <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
            <StatusBadge status="active" />
            <StatusBadge status="referred" />
            <StatusBadge status="overdue" />
          </div>
          <p style={{ margin: 0 }}>
            A <a href="#preview">link</a> and{" "}
            <span className="ref-code">CN-000012</span> reference code.
          </p>

          <div className="computed-swatches" data-testid="appearance-computed">
            {APPEARANCE_TOKENS.map((t) => (
              <span key={t.key} className="computed-swatch">
                <span
                  className="computed-swatch__chip"
                  style={{ background: computed[t.cssVar] }}
                  aria-hidden
                />
                <code data-testid={`computed-${t.key}`}>{computed[t.cssVar]}</code>
              </span>
            ))}
          </div>
          <p className="muted appearance-slider__hint">
            Effective colours on screen with the current Brightness (
            {settings.brightness}%) and Accent intensity (
            {settings.accentIntensity}%) applied.
          </p>
        </div>
      </Card>
    </div>
  );
}
