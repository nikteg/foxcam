---
name: ship
description: Finish a branch - checks, merge into main, push, back to main, delete the branch. Use when the user says "ship it", "merge into main", "merge X into main and push", "commit and push onto main", or similar.
---

# Ship a branch

1. Commit what belongs to the change (ask about anything unrelated that is lying around).
2. `mise run check` and `mise run build`. If either fails, stop and report the output.
3. UI change and not looked at yet: say so (or use the `ui-check` skill first).
4. `git checkout main`, then `git merge --ff-only <branch>`; if that fails, `git merge
   --no-edit <branch>`. Conflicts: resolve them if clear, else stop and ask.
5. Run the checks again if it was a real merge (not a fast-forward).
6. `git push origin main`. It goes over SSH, which the sandbox blocks: run it with the
   sandbox disabled. Never force push; if a push is rejected, `git pull --rebase` and
   check again, or give the user the commands.
7. `git branch -d <branch>` (and `git push origin --delete <branch>` if it was pushed).
   Remove its worktree if it had one.
8. Report: commits now on main, test count, that the push went through, and (see
   `CLAUDE.local.md`) that it will deploy, plus anything that needs doing after the deploy.

Pushing is what the user asked for here; don't push anything else in the same go.
