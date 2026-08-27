#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config_path="${SOLOSCALE_TOOLCHAIN_CONFIG:-$project_root/desktop/macos/toolchain.env}"
fail() { echo "SoloScale Desktop Build Preflight: FAIL - $*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || fail "macOS is required"
[[ -f "$config_path" ]] || fail "toolchain config is missing: $config_path"

# shellcheck disable=SC1090
source "$config_path"

: "${SOLOSCALE_TOOLCHAIN_KIND:?missing SOLOSCALE_TOOLCHAIN_KIND}"
: "${SOLOSCALE_DEVELOPER_DIR:?missing SOLOSCALE_DEVELOPER_DIR}"
: "${SOLOSCALE_SDKROOT:?missing SOLOSCALE_SDKROOT}"
: "${SOLOSCALE_EXPECTED_SWIFT_VERSION:?missing SOLOSCALE_EXPECTED_SWIFT_VERSION}"
: "${SOLOSCALE_EXPECTED_MACOS_SDK_VERSION:?missing SOLOSCALE_EXPECTED_MACOS_SDK_VERSION}"

[[ -d "$SOLOSCALE_DEVELOPER_DIR" ]] || fail "developer directory is unavailable: $SOLOSCALE_DEVELOPER_DIR"
[[ -d "$SOLOSCALE_SDKROOT" ]] || fail "macOS SDK is unavailable: $SOLOSCALE_SDKROOT"
case "$SOLOSCALE_SDKROOT" in
  "$SOLOSCALE_DEVELOPER_DIR"/*) ;;
  *) fail "mixed toolchain: SDK is outside the pinned developer directory" ;;
esac

export DEVELOPER_DIR="$SOLOSCALE_DEVELOPER_DIR"
export SDKROOT="$SOLOSCALE_SDKROOT"

swift_path="$(/usr/bin/xcrun --find swift 2>/dev/null)" || fail "Swift is unavailable from the pinned developer directory"
swift_version_output="$("$swift_path" --version 2>&1)" || fail "Swift version inspection failed"
swift_version="$(printf '%s\n' "$swift_version_output" | /usr/bin/sed -nE 's/.*Apple Swift version ([^ ]+).*/\1/p' | /usr/bin/head -1)"
sdk_version="$(/usr/bin/plutil -extract Version raw "$SOLOSCALE_SDKROOT/SDKSettings.plist" 2>/dev/null)" || fail "macOS SDK version inspection failed"

[[ "$swift_version" == "$SOLOSCALE_EXPECTED_SWIFT_VERSION" ]] || fail "Swift drifted: expected $SOLOSCALE_EXPECTED_SWIFT_VERSION, found ${swift_version:-unknown}"
[[ "$sdk_version" == "$SOLOSCALE_EXPECTED_MACOS_SDK_VERSION" ]] || fail "SDK drifted: expected $SOLOSCALE_EXPECTED_MACOS_SDK_VERSION, found $sdk_version"

if [[ "$SOLOSCALE_TOOLCHAIN_KIND" == "full-xcode" ]]; then
  [[ "$SOLOSCALE_DEVELOPER_DIR" == */Contents/Developer ]] || fail "full-xcode must point to Xcode.app/Contents/Developer"
  xcode_version="$(DEVELOPER_DIR="$SOLOSCALE_DEVELOPER_DIR" /usr/bin/xcodebuild -version | /usr/bin/head -1)" || fail "full Xcode is unavailable"
elif [[ "$SOLOSCALE_TOOLCHAIN_KIND" == "command-line-tools" ]]; then
  [[ "$SOLOSCALE_DEVELOPER_DIR" == "/Library/Developer/CommandLineTools" ]] || fail "pinned Command Line Tools path changed"
  xcode_version="not installed (explicit pinned CLT fallback)"
else
  fail "unsupported toolchain kind: $SOLOSCALE_TOOLCHAIN_KIND"
fi

cat <<EOF
SoloScale Desktop Build Preflight

Developer:
 $SOLOSCALE_DEVELOPER_DIR

Toolchain kind:
 $SOLOSCALE_TOOLCHAIN_KIND

Xcode:
 $xcode_version

Swift:
 $swift_version

Swift executable:
 $swift_path

macOS SDK:
 $sdk_version ($SOLOSCALE_SDKROOT)

Status:
 PASS
EOF
