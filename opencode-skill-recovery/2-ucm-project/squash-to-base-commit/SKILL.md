---
name: squash-to-base-commit
description: Use when the user asks to rebase onto upstream/feature_26h1 and squash all commits above it into a single commit.
---

# Rebase and Squash onto upstream/feature_26h1

## What this skill does

Fetch upstream/feature_26h1, rebase the current branch onto it, then squash all commits above upstream/feature_26h1 into a single commit. The squashed commit uses the bottom-most commit's message (the one closest to upstream/feature_26h1).

## Remote mapping

- `origin` = user's own fork (`git@github.com:yumingyue624/unified-cache-management.git`)
- `upstream` = main repo (`git@github.com:ModelEngine-Group/unified-cache-management.git`)

The base branch is ALWAYS `upstream/feature_26h1`, NOT `origin/feature_26h1`.

## Workflow

### Step 1: Fetch upstream/feature_26h1

```
git fetch upstream feature_26h1
```

### Step 2: Check commits above upstream/feature_26h1

```
git log --oneline upstream/feature_26h1..HEAD
```

This shows all commits on the current branch above upstream/feature_26h1. Confirm with the user before proceeding.

### Step 3: Save the bottom-most commit's full message

Before any reset or rebase, save the bottom-most commit's message to a temp file:

```
git log --format="%B" -1 <bottom_most_commit_hash> > C:\Users\Xuuuuuun\AppData\Local\Temp\opencode\commit_msg.txt
```

### Step 4: Rebase onto upstream/feature_26h1

```
git rebase upstream/feature_26h1
```

**IMPORTANT: Conflict resolution direction in rebase**

During `git rebase`, the meaning of `--ours` and `--theirs` is REVERSED compared to merge:
- `--ours` = the base branch (upstream/feature_26h1) — NOT what we want
- `--theirs` = our branch's changes — this is what we want to KEEP

So to keep our changes during rebase conflicts:
```
git checkout --theirs <conflicted_files>
git add <conflicted_files>
git rebase --continue
```

**CRITICAL: After rebase, ALWAYS verify the result by diffing against the original top commit:**
```
git diff <original_top_commit_hash> HEAD -- <files>
```
If there are unexpected differences, the conflict resolution went wrong and changes were lost. Fix by restoring from the original commit:
```
git checkout <original_top_commit_hash> -- <files>
git commit --amend --no-edit
```

### Step 5: Squash commits into one (if multiple commits after rebase)

If rebase produced multiple commits above upstream/feature_26h1, squash them:

```
git reset --soft upstream/feature_26h1
git commit -F C:\Users\Xuuuuuun\AppData\Local\Temp\opencode\commit_msg.txt
```

Do NOT use `git commit -m` with multi-line messages in PowerShell — it breaks on `##` prefixes and other special characters. Always use `-F` with a file.

### Step 6: Force push to origin

```
git push --force-with-lease origin <current_branch>
```

## Commit message format

The project uses this commit message format:

```
[Feat] Title

## Purpose
...

## Modifications
1. ...
2. ...

## Test
...
```