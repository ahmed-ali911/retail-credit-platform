import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

/**
 * Search-as-you-type dropdown for a Customer#/Product# lookup field (Step 15,
 * Part D). Reuses the existing `GET /customers?search=` / `GET /products?search=`
 * endpoints — no new backend surface. Still a plain text input underneath: a
 * pasted raw id or reference code (CU-.../PR-...) works exactly as before,
 * the dropdown is purely additive. `value`/`onChange` behave like `Field` —
 * the parent still owns the raw string that ultimately goes through
 * `coerceId()`.
 */
export function SearchSelect({
  label,
  value,
  onChange,
  kind,
  required,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  kind: "customer" | "product";
  required?: boolean;
  placeholder?: string;
}) {
  const [results, setResults] = useState<
    Array<{ id: number; name: string; code: string }>
  >([]);
  const [open, setOpen] = useState(false);
  const skipNextFetch = useRef(false);
  const endpoint = kind === "customer" ? "/customers" : "/products";

  useEffect(() => {
    if (skipNextFetch.current) {
      skipNextFetch.current = false;
      return;
    }
    const q = value.trim();
    if (!q) {
      setResults([]);
      return;
    }
    const timer = setTimeout(() => {
      api<Array<Record<string, unknown>>>(
        `${endpoint}?search=${encodeURIComponent(q)}`,
      )
        .then((rows) => {
          setResults(
            rows.slice(0, 8).map((r) => ({
              id: r.id as number,
              name: String(r.name ?? ""),
              code: String(r.reference_code ?? ""),
            })),
          );
          setOpen(true);
        })
        .catch(() => setResults([]));
    }, 200);
    return () => clearTimeout(timer);
  }, [value, endpoint]);

  function select(r: { id: number; name: string; code: string }) {
    skipNextFetch.current = true;
    onChange(r.code || String(r.id));
    setResults([]);
    setOpen(false);
  }

  return (
    <div className="search-select">
      <label className="field">
        <span>{label}</span>
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => results.length > 0 && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder={placeholder}
          required={required}
          autoComplete="off"
          data-testid={`search-select-${kind}-input`}
        />
      </label>
      {open && results.length > 0 && (
        <ul
          className="search-select__dropdown"
          role="listbox"
          data-testid={`search-select-${kind}-results`}
        >
          {results.map((r) => (
            <li key={r.id} role="option" aria-selected={false}>
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => select(r)}
              >
                <span className="ref-code">{r.code}</span> {r.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
