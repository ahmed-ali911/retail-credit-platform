import { useState } from "react";
import {
  APPEARANCE_TOKENS,
  DEFAULT_APPEARANCE,
  applyAppearance,
  clearAppearance,
  loadAppearance,
  saveAppearance,
  type Appearance,
} from "../lib/appearance";
import { Card } from "./ui";
import { StatusBadge } from "./StatusBadge";

/**
 * Appearance (Step 14, Part E) — a *personal, browser-local* override of the 4
 * core colour tokens. NOT a business-rule config value: it does NOT go through
 * the Step 6 maker-checker approval flow, is NOT saved on the server, and is
 * NOT shared with other users or devices.
 */
export function AppearancePanel() {
  const [colors, setColors] = useState<Appearance>(() => loadAppearance());

  function update(key: string, value: string) {
    const next = { ...colors, [key]: value } as Appearance;
    setColors(next);
    saveAppearance(next);
    applyAppearance(next);
  }

  function reset() {
    clearAppearance();
    setColors({ ...DEFAULT_APPEARANCE });
    applyAppearance(DEFAULT_APPEARANCE);
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

        <div className="appearance-grid">
          {APPEARANCE_TOKENS.map((t) => (
            <label key={t.key} className="appearance-swatch">
              <span className="appearance-swatch__label">{t.label}</span>
              <span className="appearance-swatch__row">
                <input
                  type="color"
                  aria-label={t.label}
                  data-testid={`appearance-input-${t.key}`}
                  value={colors[t.key]}
                  onChange={(e) => update(t.key, e.target.value)}
                />
                <code>{colors[t.key]}</code>
              </span>
            </label>
          ))}
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
        </div>
      </Card>
    </div>
  );
}
