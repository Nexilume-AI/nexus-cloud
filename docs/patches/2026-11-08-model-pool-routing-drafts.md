# Model Pool routing draft recovery

Switching Pools could render a previous Pool's routing draft against a different
Source list and crash while reading `deployment_id`. Drafts and previews now stay
bound to their Pool. Missing Source details remain visible as unavailable and
block submission rather than crashing the page.

The runtime fix is included in the Community frontend. The adjacent patch records
the exact original source change and browser regressions from source commit
`7e1b5dbc`. Its test fixture belongs to the hosted tree and is not bundled in the
Community application; do not apply the complete patch to this checkout.

Original validation covered four Chromium cases, TypeScript/build checks and the
hosted Model Pool entry. This publication also runs the Community type checker.
The change does not alter Source/Router access or publish any Desktop code.
