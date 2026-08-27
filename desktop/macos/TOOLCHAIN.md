# SoloScale macOS Desktop toolchain

Desktop builds do not use the ambient `xcode-select` or `DEVELOPER_DIR` state. The
canonical toolchain is declared in `desktop/macos/toolchain.env` and verified by
`scripts/check_macos_toolchain.sh` before Swift compilation starts.

The current machine has no full Xcode installation. The 2026-08-27 verified Desktop
candidate was built with the explicitly pinned Command Line Tools Swift 6.3.2 compiler
and macOS 15.4 SDK. This is a provisional but reproducible local toolchain; it is not a
claim that the app is ready for signing, notarization, or public release.

Run the preflight directly:

```bash
./scripts/check_macos_toolchain.sh
```

Build the app through the normal command:

```bash
./scripts/build_macos_app.sh
```

The build script loads the same config, overrides any ambient `DEVELOPER_DIR` and
`SDKROOT`, and invokes the pinned Swift executable. It fails before compilation if the
compiler or SDK version drifts.

When a stable full Xcode is installed, update only `desktop/macos/toolchain.env`:

```text
SOLOSCALE_TOOLCHAIN_KIND="full-xcode"
SOLOSCALE_DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
SOLOSCALE_SDKROOT="/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
```

Also replace the expected Swift and SDK versions with the values printed by the new
preflight. Do not silently fall back to Command Line Tools after that change.
