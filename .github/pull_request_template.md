## Problem and change

Link the issue, describe the trigger and explain the resulting behavior.

## Validation

Give exact commands, results, relevant comparisons, tolerances and the commit
that was tested. Explain failures and missing checks. For viewer changes, include
the affected rendering mode and representative
QVF/schema checks. For numerical changes, include reference settings, units,
tolerances and before/after comparisons. Flag changes to the contract tests
pinned by vibe-qc, as described in CONTRIBUTING.md.

## AI assistance

Disclose any AI assistance and which parts it helped produce. If none was used,
state that. Explain how you checked the submitted code, tests and references.

## Contributor confirmation

- [ ] I have read CONTRIBUTING.md, including its existing MPL-2.0 and relicensing terms.
- [ ] I have reviewed the patch and attachments for private data.
- [ ] I have added or updated the relevant tests and documentation.

Maintainer review requires green GitLab CI for the current imported head and
independent verification for numerical or release-blocking changes. The bridge
will add a GitLab MR link when GitHub contribution intake is enabled.
