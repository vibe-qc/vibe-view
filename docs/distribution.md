# Distribution builds

vibe-view uses independently versioned source releases. The initial official binary
channels are public Linux desktop downloads, the paid Mac App Store and the paid
Microsoft Store. This describes the release target; it does not claim all three
platform packages are currently available.

Covered source remains MPL-2.0. Building from published Git source requires no supporter membership.

## Package profiles

| Platform | Initial architecture | Release format |
|---|---|---|
| Linux | x86-64 | AppImage with Python, PyVista, VTK and the required browser assets |
| macOS | arm64 | Sandboxed application and signed PKG for the Mac App Store |
| Windows | x86-64 | Complete native application packaged as MSIX for the Microsoft Store |

AppImage is the initial Linux desktop format; Flatpak is deferred. CLI/server
archives must work without a desktop or FUSE. Architecture and OS minimums are
qualified separately. A package must include its required interpreter, libraries
and data; a shell that asks for Python installation is not a complete package.
Document optional external engines, remote services and excluded model stacks.

Linux desktop downloads are public and require no supporter membership or runtime activation.
Store purchases are separate and do not require website membership. Preserve
all source and redistribution rights granted by the included licenses. Required
source and notices remain accessible without an active membership.

## Build and acceptance contract

Use a native runner for each supported platform. Pin the product source revision,
Python runtime, dependencies, frontend lockfile and native toolchain. Build the
same candidate that passed the existing release CI; do not substitute a later
main revision or an old wheel bearing the same version. Reuse dependency caches
only when their platform, architecture, ABI and input hashes match.

Build recipes and non-secret locks must be included with the product source so
community builds do not require the publisher's infrastructure or credentials.
Credentials, private operations scripts, test records and deployment configuration
stay outside product packages and source snapshots.

Linux x86-64 now has its own Python/wheel lock, native library checks and bundled-runtime path handling. Windows still needs runtime qualification. macOS needs an Electron MAS build, helper signing and sandbox qualification.

Run installed-package acceptance on the oldest advertised OS in a clean account
without Python, Node, pip or development tools. Open QVF and structure files, render 3D and volume data, export a figure and close the backend cleanly.
Also test paths with spaces/non-ASCII, offline local operation, immutable install
contents, backend shutdown, update recovery and uninstall without deleting user
documents. Inventory licenses and native dependencies from the actual payload.

The initial self-contained macOS builds are ad-hoc-signed previews. See
[the Electron build guide](https://github.com/vibe-qc/vibe-view/blob/main/electron/README.md)
for existing build instructions. A successful preview does not demonstrate
Linux/Windows runtime closure, trusted signing or Store acceptance.

## Promotion

Release in this order: exact candidate source and CI, native build, installed
acceptance, channel packaging/signing, final checksums and immutable staging,
then channel publication and delivered-package verification. Give each package
revision an increasing build number; never overwrite previously released bytes.
Keep Store updates separate from website updates. A failure in one product or
platform need not hold a qualified release on another.

Mac App Store submissions require Apple identities, provisioning, appropriate
sandbox entitlements for the app and embedded helpers, and a Store-compatible
application. Electron applications use the MAS variant. A notarized DMG is a
different distribution route. See [Electron's submission guide](https://www.electronjs.org/docs/latest/tutorial/mac-app-store-submission-guide).

Microsoft Store submissions use the assigned Partner Center identity, declared
capabilities and an MSIX containing the full runtime. Qualify installed behavior
and run the Windows App Certification Kit. Microsoft signs Store-distributed
packages; local sideload testing uses its own development certificate. See
[Microsoft's signing guide](https://learn.microsoft.com/en-us/windows/msix/package/sign-msix-package-guide).

Linux desktop packages are published with checksums and corresponding source
through the public downloads page. No supporter entitlement is required. Channel
accounts, secrets, release evidence and provisioning belong to the publisher's
private operating process, not this repository.
