#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
desktop_root="$project_root/desktop/macos"
toolchain_config="${SOLOSCALE_TOOLCHAIN_CONFIG:-$desktop_root/toolchain.env}"
output_root="${SOLOSCALE_APP_OUTPUT:-$project_root/desktop/macos/dist}"
sidecar_root="${SOLOSCALE_SIDECAR_ROOT:-$project_root/packaging/macos/dist/SoloScaleBackend}"
swift_scratch="${SOLOSCALE_SWIFT_SCRATCH:-$desktop_root/.build}"
build_kind="${SOLOSCALE_BUILD_KIND:-development}"
case "$build_kind" in
  development)
    default_bundle_identifier="local.soloscale.desktop.dev"
    default_display_name="SoloScale AI OS Dev"
    ;;
  production)
    default_bundle_identifier="local.soloscale.desktop"
    default_display_name="SoloScale AI OS"
    ;;
  *)
    echo "macOS app build: SOLOSCALE_BUILD_KIND must be development or production" >&2
    exit 1
    ;;
esac
bundle_identifier="${SOLOSCALE_BUNDLE_IDENTIFIER:-$default_bundle_identifier}"
display_name="${SOLOSCALE_DISPLAY_NAME:-$default_display_name}"
app_bundle_name="${SOLOSCALE_APP_BUNDLE_NAME:-$display_name}"
app_root="$output_root/$app_bundle_name.app"
version="${SOLOSCALE_VERSION:-0.4.1}"
build_number="${SOLOSCALE_BUILD_NUMBER:-6}"
codesign_identity="${SOLOSCALE_CODESIGN_IDENTITY:-}"
git_branch="unknown"
git_commit="unknown"
if command -v git >/dev/null 2>&1 && git -C "$project_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  detected_branch="$(git -C "$project_root" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  detected_commit="$(git -C "$project_root" rev-parse --short=7 HEAD 2>/dev/null || true)"
  [[ -n "$detected_branch" ]] && git_branch="$detected_branch"
  [[ -n "$detected_commit" ]] && git_commit="$detected_commit"
fi
fail() { echo "macOS app build: $*" >&2; exit 1; }

[[ "$bundle_identifier" =~ ^[A-Za-z0-9.-]+$ ]] || fail "invalid bundle identifier"
[[ "$display_name" =~ ^[A-Za-z0-9._\ -]+$ ]] || fail "invalid display name"
[[ "$app_bundle_name" =~ ^[A-Za-z0-9._\ -]+$ ]] || fail "invalid app bundle name"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || fail "invalid version"
[[ "$build_number" =~ ^[0-9]+$ ]] || fail "invalid build number"

if [[ "${1:-}" == "--print-build-identity" ]]; then
  printf 'build_kind=%s\n' "$build_kind"
  printf 'bundle_identifier=%s\n' "$bundle_identifier"
  printf 'display_name=%s\n' "$display_name"
  printf 'app_bundle_name=%s\n' "$app_bundle_name"
  printf 'version=%s\n' "$version"
  printf 'build_number=%s\n' "$build_number"
  printf 'git_branch=%s\n' "$git_branch"
  printf 'git_commit=%s\n' "$git_commit"
  exit 0
fi

[[ "$(uname -s)" == "Darwin" ]] || fail "must run on macOS"
[[ -f "$toolchain_config" ]] || fail "toolchain config is missing: $toolchain_config"
SOLOSCALE_TOOLCHAIN_CONFIG="$toolchain_config" "$project_root/scripts/check_macos_toolchain.sh"
# shellcheck disable=SC1090
source "$toolchain_config"
export DEVELOPER_DIR="$SOLOSCALE_DEVELOPER_DIR"
unset SDKROOT
export SDKROOT="$(/usr/bin/xcrun --sdk macosx --show-sdk-path)"
swift_executable="$(/usr/bin/xcrun --find swift)"
[[ -f "$desktop_root/Package.swift" && -f "$desktop_root/Info.plist.template" ]] || fail "Swift app inputs are missing"
[[ -x "$sidecar_root/SoloScaleBackend" ]] || fail "backend sidecar is missing; run packaging/macos/build_backend_onedir.sh first"
[[ ! -e "$app_root" ]] || fail "output already exists: $app_root"
"$swift_executable" build --package-path "$desktop_root" --scratch-path "$swift_scratch" --configuration release
swift_bin_root="$("$swift_executable" build --package-path "$desktop_root" --scratch-path "$swift_scratch" --configuration release --show-bin-path)"
binary="$swift_bin_root/SoloScaleDesktop"
[[ -x "$binary" ]] || fail "Swift build did not create SoloScaleDesktop"
mkdir -p "$app_root/Contents/MacOS" "$app_root/Contents/Resources"
cp "$binary" "$app_root/Contents/MacOS/SoloScaleDesktop"
cp "$desktop_root/Info.plist.template" "$app_root/Contents/Info.plist"
/usr/bin/ditto "$sidecar_root" "$app_root/Contents/Resources/SoloScaleBackend"
/usr/bin/plutil -replace CFBundleIdentifier -string "$bundle_identifier" "$app_root/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleDisplayName -string "$display_name" "$app_root/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleName -string "$display_name" "$app_root/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleShortVersionString -string "$version" "$app_root/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleVersion -string "$build_number" "$app_root/Contents/Info.plist"
/usr/bin/plutil -replace SoloScaleBuildKind -string "$build_kind" "$app_root/Contents/Info.plist"
/usr/bin/plutil -replace SoloScaleGitBranch -string "$git_branch" "$app_root/Contents/Info.plist"
/usr/bin/plutil -replace SoloScaleGitCommit -string "$git_commit" "$app_root/Contents/Info.plist"
if [[ -n "${SOLOSCALE_GITHUB_APP_CLIENT_ID:-}" ]]; then
  /usr/libexec/PlistBuddy -c "Set :SoloScaleGitHubAppClientID $SOLOSCALE_GITHUB_APP_CLIENT_ID" "$app_root/Contents/Info.plist"
fi
[[ -x "$app_root/Contents/Resources/SoloScaleBackend/SoloScaleBackend" ]] || fail "sidecar copy failed"
if [[ -n "$codesign_identity" ]]; then
  /usr/bin/codesign --force --options runtime --timestamp --sign "$codesign_identity" "$app_root/Contents/MacOS/SoloScaleDesktop"
  /usr/bin/codesign --force --options runtime --timestamp --sign "$codesign_identity" "$app_root"
else
  # Swift linker-signs the executable before the sidecar resources are copied.
  # Seal the completed local bundle so LaunchServices sees one valid app.
  /usr/bin/codesign --force --deep --sign - "$app_root"
fi
/usr/bin/codesign --verify --deep --strict --verbose=2 "$app_root"
echo "$app_root"
