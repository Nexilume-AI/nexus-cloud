# Adding people and sharing resources in hosted Nexus Cloud

This guide describes hosted **Enterprise Access**. Organization administration is
not included in the single-owner Community distribution.

## Invite to an Organization or Project

Open **Access → People & groups → Invite to Organization / Project**.
Choose the current Organization or a specific visible Project and enter the
person's full registered Nexus email address. Review the named target, then select
**Add to Organization** or **Add to Project**.

This operation adds an existing account immediately. It does not send an email
invitation or create a pending invitation. Someone without an active account must
register first. Membership records collaboration presence; management permissions
remain separate IAM roles. Resource visibility may also depend on existing roles
and shares in that Organization or Project.

Organization additions require Organization management permission. Project
additions require management permission for that Project or Organization. The
server validates the target against the current Organization; changing an ID in
a request does not grant access to another Organization's Project.

## Add person to an existing collection

**Add person** opens even when no Project or Group has been selected. Select
**Add to**, choose the target Project or Group, and enter a registered email or
choose a current Organization member. The dialog names the target before submission.

## Why a registered account might not appear in Share

The people picker searches current Organization members, not the global account
directory. An empty result does **not** mean that a Nexus account does not exist.
Use the **Invite to Organization / Project** link, add the registered account to
the intended scope, then reopen the resource sharing dialog and search again.

Only an exact email lookup during an authorized membership addition can report
that no active account matches that email. A search failure is displayed separately
from an empty result. Editing a selected recipient clears the stale selection,
so a share cannot accidentally target the previously selected person.
