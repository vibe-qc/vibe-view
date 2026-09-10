# Desktop packaging contract

The Electron application packages the browser interface and local Python
backend into a desktop distribution. Product packaging logic remains in
`electron/`; site publication and signing credentials are external operator
configuration. See [Desktop](desktop.md) for installation and behavior.

A packaged application must start its intended backend, open a representative
QVF file, and retain its update-provider metadata. Validate each supported
platform and architecture. Test the installed application as well as an
unpacked development build; source-checkout success does not establish that
native libraries or Python resources were bundled correctly.

The contributor-facing build instructions are in `electron/PUBLISHING.md`.
Operator records and historical deployment investigations are maintained
separately in the private operations repository.
