# Web Verification Instructions

The verify agent reads this file for browser verification.

## Preview URL

If your deployment platform creates per-PR previews, document the URL pattern.

```text
Service name: web-app
PR #123 -> https://web-app-pr-123.example-preview.test
```

If no preview URL is available, use localhost:

```bash
PORT=$AUTOSYMPH_DEV_PORT pnpm dev
```

Navigate to `http://localhost:$AUTOSYMPH_DEV_PORT`.

## Authentication For Preview

Document any preview auth bypass headers or test credentials here. Keep the
actual secret values in local environment variables, not in this file.

## Playwright Setup

```bash
npx playwright install chromium
```

Use headless browser sessions for verification unless a specific test item
requires otherwise.

## Key Routes To Verify

| Route | What to check |
|-------|---------------|
| `/` | Home page loads with navigation |
| `/settings` | Settings page shows user profile |
| `/login` | Login form renders with email/password fields |

