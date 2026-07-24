# Plumbing Ref publication

The organization Gate Family rulesets load their workflow files from the
lightweight tag `refs/tags/gates/wf-v1`. The tag object is owned by this
repository's guarded publisher. Terraform owns the rulesets and the ref **name**
they consume; it does not create, move, or wrap the tag.

## Publication Request

Normal publication is a request-only pull request that changes exactly:

```text
.github/plumbing-ref/publication-request.json
```

The request records:

- `expected_current_sha`: the authoritative live tag SHA read immediately before
  creating the request;
- `target_sha`: the exact evaluated `.github` commit to publish;
- `reason`: a nonblank operational explanation;
- `references`: optional HTTPS evidence links. They are displayed but never
  fetched or interpreted as authorization or proof.

The validator requires lowercase full commit SHAs. The target must be reachable
from the current remote `main`, must be a strict descendant of the expected
commit, and must contain every workflow in
`.github/plumbing-ref/gate-family-workflows.json`. The live tag must be a
lightweight commit ref and must still equal `expected_current_sha`.

The pull-request job executes the validator from the protected base revision,
not from the proposed change, and evaluates request-only scope from the pull
request's merge base so unrelated protected-base advances do not become false
mixed-change failures. After merge, the push job uses the prior revision's
validator and directly compares the exact pushed range. The initial bootstrap is
the sole exception: when the request and validator do not exist at the before-SHA
and neither path has appeared in that revision's ancestry, the mixed
implementation PR is accepted only when
`expected_current_sha == target_sha ==` the live ref.
Bootstrap never writes the tag. The first real publication is a separate,
supervised request-only PR.

## Candidate Validation

A behavior-changing Gate workflow should receive Candidate Validation
proportional to its blast radius. The normal pattern is a temporary
`evaluate`-mode duplicate organization ruleset targeting the exact candidate
commit while the enforced rule remains on `gates/wf-v1`.

Candidate Validation is rollout guidance, not a mechanical publisher input.
Evidence links can be recorded in `reason` and `references`, but the publisher
does not dereference or judge them. Its responsibility is the deterministic
transition: shape, ancestry, complete Gate Family files, exact expected current
state, compare-and-swap, and final verification.

## Automatic publication

Merging a valid request to protected `main` starts
`plumbing-ref-publish.yml`. On the first run attempt it performs only this
mutation form:

```bash
git push --porcelain \
  --force-with-lease=refs/tags/gates/wf-v1:<expected-current-sha> \
  origin \
  <target-sha>:refs/tags/gates/wf-v1
```

The explicit ref-and-SHA lease is the race-control mechanism. Bare `--force`, a
leading `+` refspec, REST `force=true`, and tag deletion/recreation are not
allowed. The publisher requires porcelain output proving an actual update, then
rereads the authoritative remote ref and requires the exact target.

A failed push remains failed even if another operation made the target live in
the meantime. Rerunning the workflow never creates a new mutation: a rerun is
green only when the target is already live; otherwise it fails and a fresh
request is required.

Every validation and publication writes a job summary with the direction,
request, changed paths, complete Gate Family path set, affected workflow paths,
mutation state, and final observed ref. Request text is escaped before rendering.

## Broken gate blocking its own repair

If the broken required workflow blocks the reviewed request-only repair PR, an
OrganizationAdmin may use the existing ruleset bypass to merge that reviewed
request. Record why the bypass was necessary. The ordinary push-triggered
publisher still performs every mechanical check and the exact compare-and-swap;
the bypass is not Candidate Validation evidence and does not waive publisher
policy.

## Rollback

Rollback is manual and validation-first. Read the live ref immediately before
starting, then dispatch `plumbing-ref-rollback.yml` with the current live SHA as
`expected_current_sha`, the older main-ancestor commit as `target_sha`, and a
reason.

First run in the default validation mode:

```bash
gh workflow run plumbing-ref-rollback.yml \
  --repo Rubio-Enterprises/.github \
  -f expected_current_sha=<live-sha> \
  -f target_sha=<older-main-ancestor-sha> \
  -f reason='<reason>' \
  -f write_mode=validate-only
```

Review the complete plan and observed state. Then dispatch again with
`write_mode=write`. The write job reruns all validation against fresh remote
state before using the same exact-lease mutation and final reread as forward
publication.

The rollback target must be reachable from current remote `main`, must be a
strict ancestor of the expected/live commit, and must contain every Gate Family
workflow. It does not need proof of prior publication. Rollback does not edit the
Publication Request or create an append-only ledger. Re-running all jobs after a
successful write performs no mutation and reports success only when the rollback
target is already live. After rollback, the next normal request uses the actual
rolled-back live SHA as `expected_current_sha`.

## Last-resort owner recovery

Direct owner mutation is reserved for an outage where the canonical publisher
cannot run. Preserve the same safety properties: validate the exact commits and
Gate Family files, use an exact ref-and-SHA lease, inspect porcelain output, and
reread the remote ref. Never delete/recreate the tag or use an unconditional
force update. Record the incident and use the observed post-recovery live SHA as
the expected current value of the next normal request.
