#!/bin/bash
# Protected branches (main, master, tst): ask the user before committing or merging on
# one, or pushing to one. The approval is the promotion / hotfix authorization.
. "$(dirname "$0")/lib/common.sh"

hook_runs_git commit merge cherry-pick revert push || exit 0
REPO="$(hook_git_dir)"
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || exit 0
BRANCH="$(git -C "$REPO" branch --show-current 2>/dev/null)"
PROTECTED='^(main|master|tst)$'

if hook_runs_git commit merge cherry-pick revert && [[ $BRANCH =~ $PROTECTED ]]; then
  ask "'$BRANCH' is a protected branch. Commits and merges here need your approval (promotion or hotfix)."
fi

hook_runs_git push || exit 0
ARGS="$(hook_git_args push)"
case " $ARGS " in
  *" --all "*|*" --mirror "*) ask "This push includes every branch, protected ones too. Approve?" ;;
esac

# Positional arguments: remote, then refspecs. No refspec → the current branch is pushed.
POSITIONAL=()
for arg in $ARGS; do
  case "$arg" in
    -*) ;;
    *) POSITIONAL+=("$arg") ;;
  esac
done
if [ ${#POSITIONAL[@]} -le 1 ]; then
  TARGETS="$BRANCH"
else
  TARGETS="${POSITIONAL[*]:1}"
fi

for ref in $TARGETS; do
  dst="${ref##*:}"
  dst="${dst#+}"
  dst="${dst#refs/heads/}"
  [ "$dst" = "HEAD" ] && dst="$BRANCH"
  if [[ $dst =~ $PROTECTED ]]; then
    ask "This pushes to the protected branch '$dst'. Approve the promotion or hotfix?"
  fi
done
exit 0
