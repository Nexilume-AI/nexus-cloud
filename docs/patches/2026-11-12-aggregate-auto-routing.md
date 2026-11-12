# Aggregate Auto routing for hosted Nexus Cloud

Source commit: `3a7110019ed07c2d4edd5e2ab5244c1c6e909a2e`. Base: `652e53e6b052d96b99fadb7cbaf7c64edf3e1871`.

This reviewed integration patch implements Aggregate Router rule-based and native
System One cross-model decisions, the `auto` alias, candidate policy checks,
capability filtering, success-scoped tool continuity, fallback and the configuration UI.
The patch includes the complete new selector, regression tests and operating guide.

This targets the hosted Cloud source with existing System One Provider integration
and model metadata support. Community main does not yet contain that complete
baseline; do not apply this patch blindly to the Community application. It is
published in this repository's existing hosted integration patch format so the
exact deployed implementation is reviewable without replacing Community with the
private monorepo. No Desktop files, credentials or production configuration included.

Apply to the compatible source baseline:

```sh
git apply --check 2026-11-12-aggregate-auto-routing.patch
git apply 2026-11-12-aggregate-auto-routing.patch
```

Validation: 10 selector/native protocol unit tests, 33 shared routing integration
tests, and the credential-to-HTTP-upstream Auto path passed; production frontend
build passed. These do not establish an optimal latency/quality policy for every
model. See [operator guide](../hosted/aggregate-auto-routing.md).
