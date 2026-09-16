# Public source and private operations

The product source is intended to be publishable without content rewriting or
private-file exclusions. Private deployment configuration, operator records and
release evidence live outside the product repository. See CONTRIBUTING.md for
the existing contribution and external-state policies.

Canonical development history remains private. The public repository contains
separately constructed snapshots with new commit identities, preserving original
source release tags without importing development ancestry.
`PUBLIC_SOURCE_PROVENANCE.json` and `PUBLIC_SOURCE_INVENTORY.json`, added by the
publisher, bind each public snapshot to its source revision and exact file bytes.
A public commit ID and its source commit ID are different identifiers.

Publication requires exact-source qualification, privacy and secret scans,
independent review where required, immutable ref checks and verified public
readback. A local export or successful CI run alone is not publication proof.
The shared private publisher handles these checks; installation never requires
private publication tooling or credentials.
