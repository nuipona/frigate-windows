# Frigate frontend

The React/TypeScript interface that talks to the Python backend.

## Setup

```bash
npm install
```

`postinstall` runs `patch-package`. Node.js 20 matches CI.

## Development server

```bash
PROXY_HOST=<ip_address:port> npm run dev
```

`PROXY_HOST` is the Frigate instance the dev server proxies `/api`, `/vod`, `/clips` and the other backend routes to. It defaults to `localhost:5000`, which is the unauthenticated port of a Frigate running on the same machine.

## Commands

| Command | What it does |
| ------- | ------------ |
| `npm run build` | Production build for the container, served under a configurable base path |
| `npm run build:native` | Production build for the native install, served from `/`. Required for the native server to serve the UI |
| `npm run lint` | ESLint plus the Playwright spec lint |
| `npm run lint:fix` | ESLint with `--fix` |
| `npm run prettier:write` | Format sources |
| `npm run i18n:extract` | Extract `t()` keys into `public/locales/en/` |
| `npm run i18n:extract:ci` | Same, but fails if the locale files are out of date |

## E2E tests

```bash
npx playwright install chromium   # once
npm run e2e:build && npm run e2e  # build, then run the suite
npm run e2e:ui                    # interactive UI for debugging
```

## Conventions

- All user-facing strings go through `react-i18next`. English strings live in `public/locales/en/`, organized by namespace (`views/live`, `common`, ...). Never hardcode display text in a component.
- UI primitives are Radix UI/shadcn components in `src/components/ui/`, styled with TailwindCSS and the `cn()` helper.
- TypeScript runs in strict mode; avoid `any`.

The full conventions, including the generated-file rules, are in [AGENTS.md](../AGENTS.md).
