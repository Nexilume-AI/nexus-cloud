# Hosted Access dialog typing focus

Typing in Add person, Create group and other Access dialogs could move focus to
the close button after each character. A space could then close the dialog.
Form updates recreated the close callback, causing the focus effect to clean up
and rerun. The fix keeps the latest callback in a ref and initializes focus once
per mounted dialog. Escape, Tab trapping and focus restoration remain available.

The [source patch](2026-11-06-access-dialog-focus.patch) includes the exact fix
and two real-keyboard Playwright regressions. Source commit:
`b11f768e8a4e422950c46e0fb57370575d280129`; base: `ceeb9a172f908c3aeb0e8a70e9e198f5ad04b207`.
It contains only the two changed files, not the full hosted application.

## Applying the patch

This patch targets a compatible Organization-hosted Nexus source tree. Access is
an Enterprise feature; these paths are not part of the Community application in
this repository. Applying it to a Community checkout is not supported.

From the hosted source root, using the downloaded patch:

```sh
git apply --check /path/to/2026-11-06-access-dialog-focus.patch
git apply /path/to/2026-11-06-access-dialog-focus.patch
```

Build and deploy the hosted frontend through the normal deployment workflow. The
change requires no backend migration and does not alter membership, roles or
permission checks. If the check fails, reconcile the patch with your source
version rather than forcing it.

## Validation

- Both browser regressions reproduced the original failure and pass with the
  fix. They cover character-by-character email input and asynchronous search,
  Unicode insertion, selection replacement, caret editing, Tab / Shift+Tab,
  Escape and returning focus to the trigger. They submit no real membership
  changes.
- 247 focused Access and permission-boundary tests passed.
- TypeScript checking and the production frontend build passed.
- The hosted deployment was verified on 2026-11-06: source/static hashes matched,
  the public Access entry and assets returned HTTP 200, and browser checks
  confirmed continuous input and closing behavior. No production invitation or
  group was submitted during acceptance.

For the hosted user workflow, see [Adding people and sharing](../hosted/access-people.md).
