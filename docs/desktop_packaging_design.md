# Desktop packaging contract

The Electron application packages the browser interface and local Python
backend into a desktop distribution. Product packaging logic remains in
`electron/`; site publication and signing credentials are external operator
configuration. See [Desktop](desktop.md) for installation and behavior.

Apple Silicon packages now embed a relocatable CPython runtime and locked
binary wheels for the viewer, including PyVista, VTK and RDKit. The build
checks package versions, architecture and native library references both
before and after copying the runtime into the app. Downloaded Mac apps use
only that interpreter in isolated mode; a missing or damaged runtime produces
repair guidance. Source launchers keep their environment discovery.

The target is macOS 14+ on arm64, matching the dependency wheel floor.
Intel/universal Mac builds are rejected pending their own locked payloads and
acceptance. Linux and Windows runtime bundling are still separate work.
The runtime build and license inventory in `electron/runtime/README.md`
describes included capabilities, source supplements and installed acceptance.

A packaged application must start its intended backend, open a representative
QVF file, and retain its update-provider metadata. Validate each supported
platform and architecture. Test the installed application as well as an
unpacked development build; source-checkout success does not establish that
native libraries or Python resources were bundled correctly.

The contributor-facing build instructions are in `electron/PUBLISHING.md`.
Operator records and historical deployment investigations are maintained
separately in the private operations repository.
