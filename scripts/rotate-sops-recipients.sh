#!/usr/bin/env bash
# Update sops recipients across the Rubio-Enterprises fleet.
# Usage: rotate-sops-recipients.sh <old-pubkey> <new-pubkey> [--add | --replace]
#
# Modes:
#   --add     append <new-pubkey> to each .sops.yaml creation_rule (and re-encrypt
#             every *.enc.* blob); <old-pubkey> may be empty
#   --replace substitute <old-pubkey> with <new-pubkey> across each .sops.yaml
#             (and re-encrypt); use empty <new-pubkey> to remove <old-pubkey> outright
#
# Produces one branch + PR per sops-adopting repo in the fleet. Reviewer merges
# after eyeballing the diff.
set -euo pipefail
OLD="${1:-}"; NEW="${2:-}"; ACTION="${3:---replace}"
ROOT=$(mktemp -d)
cleanup() { [ -n "${ROOT:-}" ] && [ -d "$ROOT" ] && find "$ROOT" -depth -delete; }
trap cleanup EXIT

mapfile -t repos < <(gh repo list Rubio-Enterprises --limit 1000 --json name -q '.[].name')

for repo in "${repos[@]}"; do
  workdir="$ROOT/$repo"
  gh repo clone "Rubio-Enterprises/$repo" "$workdir" -- --depth=1 >/dev/null 2>&1 || continue
  pushd "$workdir" >/dev/null
  if [ -f .sops.yaml ]; then
    case "$ACTION" in
      --replace)
        if [ -n "$NEW" ]; then
          sed -i.bak "s|$OLD|$NEW|g" .sops.yaml
        else
          # Empty NEW => remove OLD entirely, plus any trailing comma whitespace.
          sed -i.bak -E "s|[[:space:]]*$OLD[[:space:]]*,?||g" .sops.yaml
        fi
        find . -maxdepth 1 -name '.sops.yaml.bak' -delete
        ;;
      --add)
        # Append NEW to each `age:` list (folded-scalar form).
        yq -i "(.creation_rules[].age) |= sub(\"\$\"; \",$NEW\")" .sops.yaml
        ;;
      *)
        echo "unknown action: $ACTION (expected --add or --replace)" >&2
        popd >/dev/null; continue
        ;;
    esac

    find . -type f \( -name '*.enc.yaml' -o -name '*.enc.json' -o -name '*.enc.env' \) \
      -exec sops updatekeys -y {} \;

    branch="chore/rotate-sops-$(date -u +%Y%m%d)"
    git checkout -b "$branch"
    git commit -am "chore: rotate sops recipients ($ACTION)"
    git push -u origin "$branch"
    gh pr create --fill --base main
  fi
  popd >/dev/null
done
