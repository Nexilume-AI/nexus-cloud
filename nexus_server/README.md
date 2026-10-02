# Nexus Community Server

The single-owner Nexus host brings Agent execution, model routing, Data Assets
and personal Computer/Mobile connections into one local Console. Agent and
Computer integrations reuse the `nexus-agent-sdk` Python package.

This independent source distribution uses the development Server package version.
It is not a hosted service or an automatically provisioned production installation.
Follow the operator prerequisites below. Do not publish the mixed
Community/Enterprise working tree or its Git history.

## Start with the Community host

Read the [operator guide](nexus_personal/HOST.md) for protected configuration,
installation preparation, database initialization, process supervision and the
remaining deployment checks. The tested Server interpreter is CPython 3.14.
Use the dedicated Community dependency locks, not the commercial distribution's
Python environment.

The independently packaged Server exposes these entrypoints:

| Command | Purpose |
| --- | --- |
| `nexus-personal-install` | Prepare/check private installation files and initialize the owner |
| `nexus-personal-process` | Run a configured Community service process |
| `nexus-personal-manage` | Run management commands in the Community host |

The operator guide distinguishes configuration from readiness. Preparing an
installation does not start services, validate a production image-admission
policy, or prove network isolation. Do not treat those separate checks as passed
because a configuration file exists.

## Personal operation

- One installation has one owner; anonymous registration is not a replacement for
  owner initialization.
- Attach your own paired Computer Runtime for files, Terminal and browser work.
  The Cloud host does not acquire access to an arbitrary computer by its address.
- Agent workloads use the existing SDK and caller-bound Run Context interfaces.
- Secrets belong in protected local configuration, not source files, command
  arguments, example accounts or a downloaded repository.

Community runtime code and the Python SDK use Apache License 2.0 (modified). Third-party components
retain their own licenses. Commercial features and the commercial deployment
entrypoints are not included in this distribution.
