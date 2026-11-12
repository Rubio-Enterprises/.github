# Recorded router API fixtures

These are projections of GitHub REST responses recorded on 12 November 2026:

- [aw-server-rust#275 chore(deps): update dependency rust to v1.99.1](https://github.com/Rubio-Enterprises/aw-server-rust/pull/275)
  supplies an image-changing pull request.
- [org-ops#404 docs: name the spec:agent state in the adr rule](https://github.com/Rubio-Enterprises/org-ops/pull/404)
  supplies a non-image change.

For each, `GET /repos/{owner}/{repo}/pulls/{number}` supplied the base repository
ID, visibility and base/head SHAs. `GET /repos/{owner}/{repo}/compare/{base}...{head}`
supplied status, commit count, base/merge-base SHAs and file metadata. Patches and
other unused fields are omitted; the retained values are not synthesized.

The tests execute the real workflow script, replacing only the GitHub API boundary.
The same recorded comparisons also test merge-group base/head selection in a
synthetic merge-group event envelope. They are not captured merge-queue deliveries
or live runner proof. Malformed data, large file lists and route declarations are
synthetic variants for boundary testing.
