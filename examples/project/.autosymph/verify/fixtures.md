# Test Fixtures And Demo Modes

The verify agent reads this file to set up test data and demo states.

## URL Test Modes

| Parameter | What it does |
|-----------|--------------|
| `?test=demo` | Loads representative demo data |
| `?test=empty` | Shows the signed-in empty state |

## JSON Fixtures

| Fixture | Location | What it provides |
|---------|----------|------------------|
| demo-state.json | fixtures/ | Representative content for visual checks |

## Test Accounts

| Account | Email | Purpose |
|---------|-------|---------|
| Test User | test@example.com | General verification |

Store passwords and tokens in local environment variables.

## Realtime-Safe Visual Fixtures

For apps that use realtime/message infrastructure, define fixture modes for
common visual states so verification can render representative UI without
consuming message quota.

| Fixture mode | What it should render | Realtime allowed? |
|--------------|-----------------------|-------------------|
| `demo` | Representative content, completed states, and input controls | No |
| `permission` | A pending permission/approval card or equivalent interrupt state | No |
| `empty` | Signed-in empty state | No |
| `realtime-live` | True end-to-end delivery through the live backend | Yes, only for explicit relay tests |

Rules:

- Screenshot-only, cosmetic, copy, layout, and accessibility checks should use
  local fixture/demo state.
- Do not send real prompts, presence events, sync payloads, or history fetches
  to create dummy visual data.
- If live realtime is required for a specific test item, call that out in the
  test plan and keep the scope narrow.

