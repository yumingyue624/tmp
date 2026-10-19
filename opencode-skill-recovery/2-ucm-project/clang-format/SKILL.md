---
name: clang-format
description: Use when formatting C/C++ code. Always use clang-format version 20 (installed via pip) to match CI requirements.
---

# Clang-Format Skill

## Overview

This skill covers how to format C/C++ code using the correct version of clang-format that matches the CI pipeline.

## Important: Version Requirement

The CI pipeline uses **clang-format version 20** (`cpp-linter/cpp-linter-action@main` with `version: 20`). Using a different version will cause CI failures due to formatting differences between versions.

## Clang-Format Executable Path

**Windows (PowerShell):**
```powershell
$clangFormat = "C:\Users\Xuuuuuun\AppData\Roaming\Python\Python314\site-packages\clang_format\data\bin\clang-format.exe"
```

This is clang-format version 20.1.0 installed via pip:
```powershell
pip install clang-format==20.1.0
```

## Usage

### Format a single file
```powershell
& $clangFormat -i --style=file <file>
```

### Format multiple files
```powershell
$files = @(
    "path/to/file1.cpp",
    "path/to/file2.h"
)
foreach ($f in $files) {
    & $clangFormat -i --style=file $f
}
```

### Check if files pass formatting (dry-run)
```powershell
& $clangFormat --dry-run --Werror --style=file <file>
```

### Format only changed lines (for commits)
When formatting code in a commit, prefer formatting only the changed lines to avoid touching unrelated code:

```powershell
& $clangFormat -i --style=file --lines <start>:<end> <file>
```

## Common Issues

### Version Mismatch
If you use the system clang-format (e.g., version 22 from `C:\Program Files\LLVM\bin\clang-format.exe`), the formatting may differ from CI expectations. Always use the pip-installed version 20.

### Include Ordering
clang-format may reorder `#include` statements. This is expected behavior based on the `.clang-format` configuration.

### Brace Wrapping
The project's `.clang-format` uses `BraceWrapping: AfterFunction: true`, which means function opening braces should be on a new line. However, `AllowShortFunctionsOnASingleLine: All` allows very short functions to remain on one line. clang-format 20 and 22 may differ on edge cases.

## Verification

After formatting, verify with:
```powershell
& $clangFormat --dry-run --Werror --style=file <file>
```

If this command produces no output, the file passes formatting checks.
