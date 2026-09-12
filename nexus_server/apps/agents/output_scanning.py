"""Bounded text-rule scan. Unknown/oversized lexical spans fail closed."""
import codecs
import hashlib
import re


# Existing rules have unbounded token lengths. Reject exceptionally long runs of
# their possible characters instead of silently missing a cross-window secret.
SPAN_PATTERNS = (re.compile(r"[A-Za-z0-9._%+@/~=\-]+"), re.compile(r"[\d\s().+\-]+"), re.compile(r"\s+"))
OVERLAP = 16384
CHUNK = 256 * 1024


def scan_output_stream(stream):
    from apps.common.redaction import redact_payload
    digest = hashlib.sha256()
    size = 0
    findings = 0
    tail = ""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    error_code = ""
    while True:
        chunk = stream.read(CHUNK)
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
        if error_code:
            continue
        try:
            text = tail + decoder.decode(chunk)
        except UnicodeDecodeError:
            error_code = "BINARY_SCANNER_REQUIRED"
            continue
        if "\x00" in text:
            error_code = "BINARY_SCANNER_REQUIRED"
            continue
        if any(match.end() - match.start() >= 4096 for pattern in SPAN_PATTERNS for match in pattern.finditer(text)):
            error_code = "SCAN_LEXICAL_SPAN_TOO_LONG"
            continue
        _, stats = redact_payload(text)
        # Overlap can encounter a finding twice; the gate uses a boolean, not an
        # exact count. Do not advertise duplicate window hits as distinct findings.
        findings = max(findings, int(stats["replacement_count"] > 0))
        tail = text[-OVERLAP:]
    if not error_code:
        try:
            _, stats = redact_payload(tail + decoder.decode(b"", final=True))
            findings = max(findings, int(stats["replacement_count"] > 0))
        except UnicodeDecodeError:
            error_code = "BINARY_SCANNER_REQUIRED"
    return {"size_bytes": size, "sha256": digest.hexdigest(), "finding_count": findings,
            "replacement_count": findings, "sensitive_key_count": 0, "error_code": error_code}
