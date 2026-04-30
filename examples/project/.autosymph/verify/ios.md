# iOS Verification Instructions

The verify agent reads this file for iOS simulator verification.

## Build

```bash
WORKSPACE="ios/App/App.xcworkspace"
SCHEME="App"

xcodebuild \
  -workspace "$WORKSPACE" \
  -scheme "$SCHEME" \
  -sdk iphonesimulator \
  -destination "platform=iOS Simulator,name=$AUTOSYMPH_SIM_NAME" \
  -derivedDataPath "$AUTOSYMPH_DERIVED_DATA" \
  -configuration Debug \
  build
```

## Install And Launch

```bash
APP_PATH="$AUTOSYMPH_DERIVED_DATA/Build/Products/Debug-iphonesimulator/App.app"
BUNDLE_ID="com.example.app"
```

1. Install with `mcp__ios-simulator__install_app` using `APP_PATH`.
2. Launch with `mcp__ios-simulator__launch_app` using `BUNDLE_ID`.

## Cross-Platform Shells

If the native app wraps web assets, document the asset build/sync step here.

```bash
# Example only; replace with your project commands.
pnpm build
pnpm exec cap sync ios
```

## Key Accessibility Labels

| Element | Accessibility Label |
|---------|---------------------|
| Sign in button | Sign In |
| Settings tab | Settings |

## Test Modes

If the app supports fixture modes, document the launch arguments, environment
variables, or webview storage keys that activate them. Prefer fixture modes for
visual verification that does not need live backend traffic.

