# A hosted Agent works on the caller's Computer

Captured on 2026-10-06 using the existing local Nexus Cloud Enterprise installation.
The three GIFs are edited real-browser keyframes, with reading holds and omitted
waits. No UI values, messages, files or success states were painted into the frames.
Minor browser pane size differences were padded, not stretched or cropped.

## The story

1. A developer uploads one [Python file](../../examples/readme_computer_agent.py).
   The existing Python build service builds and deploys it to a real Docker runtime.
2. A caller discovers that published, free Agent in Marketplace, approves three
   file scopes and attaches an online Computer.
3. The Agent reads a CSV through the caller-bound Run Context, updates Plan and
   asks for permission to write a report. The caller answers in Private Display.
4. The report is written to the Windows Computer. A matching private Run artifact
   is scanned, previewed and downloaded. Refresh restores the same Run and file.

The developer and caller use separate demo Organizations. The caller used an
existing platform-admin test identity in a newly authorized, isolated Organization;
these captures demonstrate functionality, **not a least-privilege or tenant-isolation
security test**. No existing business resources were modified.

## Reproduce

Prerequisites: a configured Python build profile, Docker execution worker, private
file storage and an online Nexus Computer Runtime. Real remote devices need working
HTTPS/WSS. Pairing and Agent authorization are separate steps.

1. Upload `readme_computer_agent.py` through **Build > Agents > Runtime > Upload
   Python**, build, deploy and wait for a healthy runtime. No model key is needed.
2. On an Enterprise installation, configure the version, free pricing and listing,
   then explicitly publish. Discover it in Marketplace. On a self-hosted single-owner
   installation, use the Agent's private test entry in Build instead of Marketplace.
3. Pair the Computer Runtime with a **new isolated test root**. Use **Attach Computer**
   to approve `files.list`, `files.read`, `files.write` and select that device.
   The example does not request terminal, browser or mobile permissions.
4. Put the provided [expenses.csv](../../examples/expenses.csv) in the **Agent's
   Workspace**, not directly in the paired Computer root. In this installation its
   path is `<paired-root>/agents/<cloud-agent-id>/workspace/expenses.csv`.
   Confirm the current Workspace in Private Display before running.
5. Open Private Display and send:

   > Turn my expenses.csv into a short report. Ask before writing it.

6. Check the three records and $128.50 total, then select **Save my report**.
   Choosing **Do not write** leaves the input unchanged and produces no report.
7. Confirm `expense-report.md` is beside the CSV on the Computer. This example also
   uploads a private copy for the Run's Files panel; these are two explicit SDK
   operations, not a shared filesystem mount.
8. This installation requires an authorized operator to scan the completed output
   before preview/download. The capture used the existing output-scan API as the
   demo publisher; it did not alter scan/policy fields in the database or bypass
   the approval check. Open Files after the scan is passed and policy approved.
9. Preview, download and refresh the Run. Compare the report on the Computer with
   the private artifact and downloaded file, then verify the CSV is unchanged.

## Verified outcome

| Check | Result |
| --- | --- |
| Execution | Real Docker-hosted Python Agent, healthy runtime |
| Device | Real Windows Computer Runtime, outbound connection, isolated Workspace |
| Marketplace | Discovery, explicit scope approval and Attach completed in the UI |
| Run | Completed after the inline confirmation |
| Input | 3 synthetic records; original CSV unchanged |
| Result | $128.50 total; 370-byte Markdown report written on the Computer |
| Artifact | Private Run copy scanned and approved through the normal API |
| Integrity | Computer file, Run artifact and browser download SHA-256 match |
| Reload | Same completed Run and selected report remain available |

The [manifest](device-demo-manifest.json) records source, input, output and GIF
checksums. The final source was rebuilt through the same build service before the
successful Run. Initial fixture attempts exposed a CSV placed outside the Agent's
Workspace and a missing explicit Confirm choice list in the new example. Both
were corrected; offline tests also cover decline and invalid-input behavior.
Marketplace/Attach frames precede those fixture corrections; execution/result
frames show the final successful Run. This is not an uncut single-take recording.

## Boundaries

- This is a deterministic SDK workflow, not an LLM reasoning or speed benchmark.
- The input is synthetic. No personal files, credentials or user home directories
  appear in the public assets; no personal device data was used.
- File contents pass through the authorized Cloud execution path. Decoupling does
  not mean the Cloud or Agent never processes file contents.
- The Agent was not installed on the Computer. The Computer Runtime was installed
  separately. Agent and Computer were on the same physical test host but used
  distinct Docker/Windows execution environments; this capture does not establish
  cross-NAT or geographic-network behavior.
- Marketplace and commercial administration belong to Enterprise; the repository's
  single-owner edition has its own Build entry. The GIF is not an edition matrix.
- No Mobile, Browser, Terminal, OpenWrt, multi-caller concurrency, billing or model
  routing behavior is asserted by this demonstration.
- The prior device-free capture and its original assets remain available in
  [capture-notes.md](capture-notes.md); their provenance is unchanged.
