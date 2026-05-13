# rubio-dotgithub


## scripts/

- `rotate-sops-recipients.sh <OLD> <NEW> [--add|--replace]` — fan-out helper for §6.10 key rotation. Produces N reviewable PRs (N = current sops-adopting repo count) with `sops updatekeys -y` already applied per-repo. v6 reality: N = 0 at Phase-1 publication; grows as repos opt in.

### Common usage patterns

The script is the fleet-wide primitive for the per-host key architecture (Plan 1 header + §8.10 appendix). Three operations:

**Onboarding a new persistent host** (new Mac, new tailnet VM joins the fleet):
```bash
# After running Plan 1 Task 20 Step 4 on the new host and recording its pubkey:
bash rotate-sops-recipients.sh "" "<NEW_HOST_PUB>" --add
```
`--add` is non-destructive — it appends the new pubkey to each blob's recipient list without removing anyone. Use this when a new operator host or prod deploy target joins.

**Decommissioning a host** (Mac dies, VM destroyed, key compromise):
```bash
bash rotate-sops-recipients.sh "<OLD_HOST_PUB>" "" --replace
```
`--replace` substitutes the old pubkey with the new one (empty `NEW` means "remove old without adding anything"). Use this when a host leaves the fleet.

**Key rotation** (CI key compromised, scheduled hygiene):
```bash
# Generate new key, then:
bash rotate-sops-recipients.sh "<OLD_CI_PUB>" "<NEW_CI_PUB>" --replace
gh secret set SOPS_AGE_KEY --org Rubio-Enterprises --visibility=all --body "$(cat new-ci-keyfile.txt)"
```

After any of the three, run `copier update --skip-answered --vcs-ref=v1` against the fleet so `.sops.yaml` files re-pin to the updated recipient list (Plan 2 Task 12 established the fleet-wide rollout pattern).
