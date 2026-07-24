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

## Last-resort direct owner recovery

> **Last resort:** there is intentionally no automated rollback workflow or CLI
> command. A repository owner may perform the direct recovery below only when the
> normal forward Publication Request publisher is unavailable and leaving the
> current Gate workflow set live is more harmful than direct ref mutation.

Use a clean clone with owner authorization. Select an older target commit from
`.github/main`, then run every block below in the same shell without substituting
an automated workflow, API force update, tag deletion/recreation, PAT, App,
environment, Terraform wrapper, concurrency mechanism, or workflow dispatch.

1. Reread the authoritative live ref and fetch fresh remote state. The live ref,
   not the last Publication Request, supplies `EXPECTED_CURRENT_SHA`.

   ```bash
   set -euo pipefail
   REMOTE=origin
   LIVE_REF=refs/tags/gates/wf-v1
   TARGET_SHA=<older-full-commit-sha>

   python3 - "$TARGET_SHA" <<'PY'
   import re
   import sys

   target = sys.argv[1]
   if re.fullmatch(r"[0-9a-f]{40}", target) is None or target == "0" * 40:
       raise SystemExit("TARGET_SHA must be a nonzero full lowercase commit SHA")
   PY

   read_remote_ref() {
     python3 - "$REMOTE" "$LIVE_REF" <<'PY'
   import re
   import subprocess
   import sys

   remote, ref = sys.argv[1:]
   result = subprocess.run(
       ["git", "ls-remote", "--refs", remote, ref],
       check=True,
       capture_output=True,
       text=True,
   )
   lines = [line.split() for line in result.stdout.splitlines() if line.strip()]
   if len(lines) != 1 or len(lines[0]) != 2 or lines[0][1] != ref:
       raise SystemExit(f"authoritative remote {ref} must exist exactly once")
   sha = lines[0][0]
   if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
       raise SystemExit(f"authoritative remote {ref} returned an invalid SHA")
   print(sha)
   PY
   }

   EXPECTED_CURRENT_SHA="$(read_remote_ref)"
   git fetch --quiet --force --no-tags "$REMOTE" \
     "refs/heads/main:refs/remotes/plumbing-recovery/main" \
     "$LIVE_REF:refs/plumbing-ref/recovery-live"
   test "$(git rev-parse refs/plumbing-ref/recovery-live)" = \
     "$EXPECTED_CURRENT_SHA"
   test "$(git cat-file -t refs/plumbing-ref/recovery-live)" = commit
   test "$(git cat-file -t "$TARGET_SHA")" = commit
   ```

2. Prove that the target is both reachable from authoritative `.github/main`
   and strictly older than the reread live commit.

   ```bash
   test "$TARGET_SHA" != "$EXPECTED_CURRENT_SHA"
   git merge-base --is-ancestor \
     "$TARGET_SHA" refs/remotes/plumbing-recovery/main
   git merge-base --is-ancestor \
     "$TARGET_SHA" refs/plumbing-ref/recovery-live
   ```

3. Verify every Gate Family workflow in the manifest from authoritative
   `.github/main` is a blob at the target.

   ```bash
   python3 - "$TARGET_SHA" <<'PY'
   import json
   import subprocess
   import sys

   target = sys.argv[1]
   manifest_result = subprocess.run(
       [
           "git",
           "show",
           "refs/remotes/plumbing-recovery/main:"
           ".github/plumbing-ref/gate-family-workflows.json",
       ],
       check=True,
       capture_output=True,
       text=True,
   )
   manifest = json.loads(manifest_result.stdout)
   if not isinstance(manifest, dict) or not manifest:
       raise SystemExit("Gate Family workflow manifest must be a nonempty object")
   for family, workflow_path in sorted(manifest.items()):
       result = subprocess.run(
           ["git", "cat-file", "-t", f"{target}:{workflow_path}"],
           capture_output=True,
           text=True,
       )
       if result.returncode != 0 or result.stdout.strip() != "blob":
           raise SystemExit(
               f"{family} workflow is missing or not a blob: {workflow_path}"
           )
       print(f"verified {family}: {workflow_path}")
   PY
   ```

4. Perform exactly one ref-and-SHA lease update, capture its complete result,
   and always reread the authoritative remote ref even when the push is rejected.
   Success requires all three conditions: push status zero; exactly one porcelain
   update record for `${TARGET_SHA}:${LIVE_REF}` beginning with `+` and reporting
   a forced update, never `=`; and a final authoritative SHA equal to
   `TARGET_SHA`. Never retry with a stale expected SHA.

   ```bash
   # BEGIN owner-recovery-cas
   set +e
   PUSH_OUTPUT="$(
     git push --porcelain \
       "--force-with-lease=${LIVE_REF}:${EXPECTED_CURRENT_SHA}" \
       "$REMOTE" \
       "${TARGET_SHA}:${LIVE_REF}" \
       2>&1
   )"
   PUSH_STATUS=$?
   FINAL_OBSERVED_SHA="$(read_remote_ref 2>&1)"
   FINAL_READ_STATUS=$?
   set -e

   printf 'push exit status: %s\n%s\n' "$PUSH_STATUS" "$PUSH_OUTPUT"
   printf 'final reread exit status: %s\n' "$FINAL_READ_STATUS"
   printf 'final observed ref: %s\n' "$FINAL_OBSERVED_SHA"

   python3 - \
     "$PUSH_STATUS" \
     "$FINAL_READ_STATUS" \
     "$LIVE_REF" \
     "$TARGET_SHA" \
     "$FINAL_OBSERVED_SHA" \
     "$PUSH_OUTPUT" <<'PY'
   import sys

   push_status = int(sys.argv[1])
   final_read_status = int(sys.argv[2])
   live_ref = sys.argv[3]
   target = sys.argv[4]
   final_observed = sys.argv[5]
   push_output = sys.argv[6]
   records = []
   for line in push_output.splitlines():
       fields = line.split("\t")
       if fields[0] in {"+", "=", "!", "-", "*", " "}:
           records.append(fields)

   errors = []
   if push_status != 0:
       errors.append(f"git push exited with status {push_status}")
   if any(record[0] == "=" for record in records):
       errors.append("porcelain reported '=' up-to-date, not an actual update")
   if len(records) != 1:
       errors.append(f"expected exactly one porcelain update record, got {len(records)}")
   else:
       record = records[0]
       if record[0] != "+":
           errors.append(f"porcelain update status was {record[0]!r}, not '+'")
       if len(record) < 3 or record[1] != f"{target}:{live_ref}":
           errors.append("porcelain update record did not target the exact live ref")
       if len(record) < 3 or "(forced update)" not in record[2]:
           errors.append("porcelain update record did not report a forced update")
   if final_read_status != 0:
       errors.append(f"authoritative final reread exited with {final_read_status}")
   elif final_observed != target:
       errors.append(
           f"authoritative final ref was {final_observed}, expected {target}"
       )
   if errors:
       raise SystemExit("owner recovery verification failed:\n- " + "\n- ".join(errors))
   print(f"verified exact owner recovery update to {final_observed}")
   PY
   # END owner-recovery-cas
   ```

Record the incident and the observed final SHA outside the publisher. The next
normal Publication Request must reread the actual live ref and use that SHA as
`expected_current_sha`; the request document is not desired state and must not
be assumed to describe the post-recovery ref.
