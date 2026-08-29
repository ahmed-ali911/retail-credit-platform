# Retail Credit — Staff Web App (Step 7)

React + Vite front end for internal staff. Covers the **core flow only**:
login → create customer/product → application + assessment → offer + accept →
contract, delivery, payment. See the "Frontend" section of the repo root
[README](../README.md) for the full run instructions and the covered-vs-deferred list.

```bash
cp .env.example .env      # VITE_API_URL -> backend (dev server proxies /api to it)
npm install
npm run dev               # http://localhost:5173
npm test                  # Vitest + React Testing Library
npm run typecheck
```

Colour is centralised in [`src/styles/tokens.css`](src/styles/tokens.css) — adjust there, not in components.
