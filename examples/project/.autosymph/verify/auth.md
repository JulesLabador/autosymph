# Authentication Instructions

The verify agent reads this file when a test plan is auth-gated.

## Login Method

Choose the method your project supports and replace the placeholders.

### Option A: Test Credentials

```text
Email: test@example.com
Password: stored in a project-specific environment variable
```

### Option B: Auth Bypass Token

```bash
# Example preview-deployment bypass header, if your platform supports one.
x-preview-protection-bypass: $PREVIEW_BYPASS_SECRET
```

### Option C: Fixture Or Demo Login

Document launch arguments, URL parameters, localStorage keys, or seed commands
that create a signed-in verification state without using production accounts.

## Steps To Authenticate

1. Navigate to the login entry point.
2. Apply the selected credential, bypass, or fixture setup.
3. Confirm the signed-in screen is visible.

## Realtime / Message-Quota Safety

For auth-gated visual verification, prefer a signed-in fixture or demo mode that
does not connect production realtime transports or publish messages.

If a test item is cosmetic, layout-only, screenshot-only, or copy-only, do not
use a live relay or live realtime channel just to create sample messages. Use
the fixture/demo path from `.autosymph/verify/fixtures.md`.

## Verify Auth Succeeded

After login, the verifier should see:

- The expected signed-in landing screen.
- A stable user/session indicator.
- No blocking permission, paywall, or onboarding screen unless that is the item
  under test.

