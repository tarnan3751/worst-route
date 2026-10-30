#!/usr/bin/env bash
# Lands a delivered patch series on the main tree: the coordinator's landing step
# (CLAUDE.md, **Agent workflow**). A developer delivers `git format-patch` files on a base
# commit, one commit each, and reports the tree they reproduce. This script applies them
# with `git am`, checks the result against that tree, runs the gate, and leaves the
# commits in place for the owner to push.
#
#   tools/land_series.sh <patch-dir> <base-commit> <tree-hash> [--dry-run]
#
# It refuses a working tree that is not clean, a HEAD that is not the base commit, a
# branch other than main, a patch directory with no NNNN-*.patch files, a message that
# carries a trailer or whose subject ends with a period, a patch that does not apply, and
# a final tree that differs from the reported one. Each refusal after `git am` started
# resets the branch back to the base, so the tree is left as it was found. `--dry-run`
# applies and checks the chain, then resets to the base without running the gate. A gate
# failure leaves the commits in place and says how to undo them.
set -uo pipefail

usage() {
  echo "usage: tools/land_series.sh <patch-dir> <base-commit> <tree-hash> [--dry-run]" >&2
  exit 2
}

dry_run=0
positional=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    -*) echo "land_series: unknown option $arg" >&2; usage ;;
    *) positional+=("$arg") ;;
  esac
done
[ "${#positional[@]}" -eq 3 ] || usage
patch_dir="${positional[0]}"
base_arg="${positional[1]}"
expect_tree="${positional[2]}"

# The repository is the one the script is run in, never the script's own location, which
# would land a series meant for a clone on the main tree.
root="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "land_series: not in a git repository" >&2; exit 1; }
cd "$root"
tools_dir="$root/tools"

refuse() {
  echo "land_series: $*" >&2
  exit 1
}

branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" = "main" ] || refuse "HEAD is on $branch, not main"
head="$(git rev-parse HEAD)"
base="$(git rev-parse --verify "${base_arg}^{commit}" 2>/dev/null)" || refuse "no such commit $base_arg"
[ "$head" = "$base" ] || refuse "HEAD is ${head:0:10}, not the base ${base:0:10}"
if [ -n "$(git status --porcelain)" ]; then
  git status --short >&2
  refuse "the working tree is not clean"
fi

[ -d "$patch_dir" ] || refuse "no patch directory $patch_dir"
patches=()
while IFS= read -r file; do
  patches+=("$file")
done < <(find "$patch_dir" -maxdepth 1 -name '[0-9][0-9][0-9][0-9]-*.patch' | sort)
[ "${#patches[@]}" -gt 0 ] || refuse "no NNNN-*.patch files in $patch_dir"

# Message checks before anything is applied: the commits carry no trailers, and a
# subject is an imperative sentence with no trailing period.
n=0
for patch in "${patches[@]}"; do
  n=$((n + 1))
  message="$(sed -n '/^Subject:/,/^---$/p' "$patch")"
  subject="$(grep -m1 '^Subject:' "$patch" | sed -E 's/^Subject: (\[PATCH[^]]*\] )?//')"
  [ -n "$subject" ] || refuse "patch $n has no Subject line"
  if printf '%s' "$subject" | grep -qE '\.[[:space:]]*$'; then
    refuse "patch $n's subject ends with a period: $subject"
  fi
  if printf '%s\n' "$message" | grep -qiE '^(Co-Authored-By|Signed-off-by|Generated-with|Reviewed-by|Claude-Session):'; then
    refuse "patch $n's message carries a trailer"
  fi
  wide="$(printf '%s\n' "$message" | awk 'length > 100' | wc -l | tr -d ' ')"
  [ "$wide" -eq 0 ] || echo "note: patch $n has $wide message line(s) over 100 characters"
done

echo "repository $root, branch main at ${base:0:10}, ${#patches[@]} patch(es)"

rollback() {
  git am --abort >/dev/null 2>&1 || true
  git reset -q --hard "$base"
  echo "land_series: main reset to the base ${base:0:10}" >&2
}

if ! git am "${patches[@]}"; then
  rollback
  refuse "a patch does not apply (see above)"
fi

tree="$(git rev-parse 'HEAD^{tree}')"
case "$tree" in
  "$expect_tree"*) ;;
  *)
    case "$expect_tree" in
      "$tree"*) ;;
      *)
        rollback
        refuse "the chain's final tree $tree differs from the reported tree $expect_tree"
        ;;
    esac
    ;;
esac
echo "final tree ${tree:0:12} matches the reported tree"
git log --reverse --format='%h %s' "$base..HEAD" | nl -w2 -s' '

if [ "$dry_run" -eq 1 ]; then
  git reset -q --hard "$base"
  echo "dry run: main stays at ${base:0:10}"
  exit 0
fi

echo "running the gate"
if ! "$tools_dir/gates.sh"; then
  echo "land_series: the gate failed; the commits are in place. To undo: git reset --hard ${base:0:10}" >&2
  exit 1
fi
new_head="$(git rev-parse HEAD)"
echo "main: ${base:0:10} -> ${new_head:0:10} (${#patches[@]} commits), not pushed"
