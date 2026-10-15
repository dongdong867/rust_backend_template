# Maintainer command security and validation

The only command forms are `/lint`, `/test` and `/test-{crate}`.
See [the README](../README.md#maintainer-requested-checks) for their behavior and [Make](../Makefile) for local checks.

## Trust boundaries

`ci-command.yml` handles newly created issue comments.
`ci-command-completion.yml` independently handles completed runs of `ci-command-worker.yml`.
Its trigger accepts the static worker name and dynamic `ci-command *` run names; it does not call back into the dispatcher or trigger itself.
GitHub's completed-run names can differ from the workflow's configured name, so a static-name-only callback is insufficient evidence of delivery.
Its read-only route job checks exact syntax, an open PR and the commenter's current repository write, maintain or admin permission.
Rejected commands never enter the controller or dispatch a worker.
The controller rechecks the comment and permission before accepting it.
An accepted request contains a PR number, command, comment ID, accepting controller run ID, exact PR head SHA and trusted worker workflow SHA.
The controller uses REST API version `2026-03-10` and binds ownership to the worker ID returned by dispatch; a legacy response without an ID fails closed.

Only the dispatcher has `actions: write` for dispatch/cancellation.
Both trusted control jobs can write statuses, and only completion reporting has `pull-requests: write` to maintain a result comment.
Both check out only `github.sha`, a trusted default-branch commit for their respective trigger types.
Neither evaluates PR source, Makefiles, toolchain files, caches or artifacts.
Workers have only read permissions and non-persisted checkout credentials.
No production secret, writable token, privileged environment, self-hosted runner or shared writable cache is used for PR execution.
Do not enable private-fork settings that send write tokens or secrets to PR workflows.
An authorized comment is explicit permission to execute the captured code; default fork-workflow approval settings do not replace this authorization.

Workers validate their bound ID and durable accepted request before execution admission and again before checking out code.
Admission never automatically cancels another worker.
Only the controller cancels recorded obsolete worker IDs for the same PR and command; a delayed older job cannot cancel the newer owner.
A worker briefly retries when startup precedes registration visibility, but never infers ownership from matching metadata.
They fetch the captured PR SHA through the base repository, verify the checkout SHA and execute the requested Make targets.
All PR evaluation, including tool-pin discovery, happens only after the read-only boundary.
Each worker's definition and validation code come from the default branch, not its PR.
Passing results require the bound worker ID, expected workflow ID/path, repository, default branch, initial run attempt, request metadata and recorded workflow SHA.
The bound owner's unsupported rerun or dispatch-ref mismatch is an explicit status error, not an indefinitely missing result.
An unsupported rerun invalidates that owner's earlier passing result, but cannot change a newer request's shared-SHA status.
Success also requires both the validation and execution jobs to have succeeded; a skipped execution job cannot count as passing tests.
The reporter never downloads or trusts worker-produced artifacts.
Manual worker dispatches and Actions reruns are not the supported request interface; create a new command comment.

## Result comments

Completion reporting maintains one bot-owned comment per PR and command, headed `[Passed]` or `[Failed]`, with no emojis.
The comment identifies the captured commit, full/lint/package scope, duration, logs and safe failure reason.
Counts are test executions per feature mode, not unique tests across modes.
Partial comments explicitly cannot satisfy `/test`; coverage is not collected in this version.
When an existing comment is edited, GitHub may not send a new notification.

Workers stream ordinary Make output and emit a bounded JSON summary line into their logs.
The reporter reads only the bound execution job's small log, never an artifact or executable file.
API credentials are sent only to the initial API request and are removed before following HTTPS log-storage redirects.
Missing, oversized or malformed summaries mean counts are unavailable, not zero.
Only bounded numeric values, known stages and validated test identifiers can reach Markdown; raw diagnostics are never pasted.
These summaries are untrusted and advisory. They never determine authorization, status or whether a commit can merge.
Status is finalized from validated GitHub run/job metadata before comment publication; a comment API failure can be retried without reverting that status or duplicating the comment.
The per-PR cursor records the terminal reason before comment publication, so replay does not guess a different explanation from later PR or worker metadata.
New `v3` cursor descriptions append one validated reason code; existing `v2` cursors remain readable, but their unrecorded terminal reasons display `Reason unavailable`.
The reason is separate from request identity and fits within GitHub's 140-character status-description limit.
The latest per-PR request owns its result comment, even where another PR owns the same SHA's shared status.

## Why cancellation alone is insufficient

GitHub concurrency does not guarantee dispatch ordering.
Reading the latest comment and then writing a status from independent reporters leaves a race.
The same non-cancelling repository/command controller lock therefore owns acceptance, cancellation, pending statuses and final statuses across PRs.
Workers have no cancelling concurrency group, so they cannot cancel another worker or the status writer.
The controller uses `queue: max`, which preserves up to 100 pending controller jobs, rather than the default replacement of a single pending job.
Overflowed, manually cancelled or failed controller runs need a new comment; they must not be treated as successful checks.

Commit statuses are shared when two PRs have the same head SHA.
The newest accepted comment ID for a SHA/command owns its visible status; older PR completions cannot overwrite that pending or terminal result.
Per-PR cursors and worker cancellation remain independent even when the commit status is shared.
Package contexts include a command hash because GitHub context names are case-insensitive while Cargo package names are exact selectors.

GitHub commit statuses on the repository's single root commit hold durable `ci-command-state/<PR>/<command-hash>` cursors.
A full-history trusted checkout identifies this stable anchor, so changing either the PR head or the default branch cannot reset request ordering.
These are internal bookkeeping contexts, not required checks.
The comment ID orders accepted requests. Older events cannot replace a newer cursor.
Every completion reconciles the currently accepted request from GitHub run metadata, even when the event belongs to an older worker.
Terminal results are idempotent, and a new request first replaces its head-SHA result with pending.
Cancellation skips already completed historical owners and tolerates deleted obsolete runs; archived history cannot permanently block a fresh command.
Only Actions-authored cursor and result records are accepted; corrupt or foreign state fails closed rather than silently resetting ownership.
Repositories with multiple root commits are refused; do not rewrite repository history while command runs are active.
GitHub's status-count limits still apply; API failures are nonpassing and require inspection, not a success fallback.

## Workflow checker compatibility

Pinned actionlint `1.7.12` does not yet recognize GitHub's documented `queue: max` field.
`make ci-config-check` runs that checker on the original workflow files and validates the two canonical non-cancelling control mappings separately.
Only its exact unsupported-key diagnostic at each named, validated location is exempted; wrong values, alternate placement, duplicate keys and all other diagnostics still fail.
No YAML is stripped or rewritten before checking.
Remove the compatibility exception when a released checker natively supports the field.

## Required check rollout

The dispatcher, worker and completion workflows must first reach the default branch.
Review the privilege boundaries, state/status writer serialization, request matching and workflow pins before making a check required.
Keep `ci-comment /test` non-required until the following hosted evidence is collected on the exact installed workflow version:

1. An unauthorized exact command, a normal issue comment, an edited comment and extra whitespace do not dispatch work or write statuses.
2. An authorized `/lint` and `/test` produce separate statuses on the captured PR head; deliberately failing lint and tests report failure.
3. `/test-{crate}` selects a workspace member only, runs its supported feature modes and uses a partial context distinct from `ci-comment /test`. An unknown package fails clearly.
4. Two identical commands cancel/supersede the older execution. Its late cancellation or completion cannot overwrite the newer pending or terminal result.
5. Different commands, different packages and different PRs run independently.
6. A head update leaves the new commit without a passing full-test status until a fresh `/test` succeeds. A stale worker never reports success on that new SHA.
7. The base repository can fetch the exact head and attach statuses for a fork PR. Repeat on a private fork when private-fork support is needed.
8. Cancellation, timeout, skipped jobs and unavailable API calls cannot yield success. Observe cold-run elapsed and billed time; no cache or runtime guarantee is assumed.
9. Generated repositories with and without the example retain the byte-identical workflows/helpers and the package test target, with their generated service name.
10. Two PRs sharing a head SHA execute independently, but only the newest accepted request can change that SHA/command's visible result. A closed older PR cannot overwrite it.
11. The dispatch response's worker ID owns the request; duplicate dispatches and reruns cannot take over. A changed dispatch base becomes a nonpassing result.
12. A successful worker actually triggers completion reporting and its status becomes terminal. `[Passed]`/`[Failed]` result comments show safe metrics, unavailable/not-run data accurately, and only the latest result updates the same command comment.

Then, with separate repository-settings approval, require only `ci-comment /test` on the integration branch.
Select GitHub Actions as its expected source where GitHub offers that setting.
Do not use the controller's default-branch job checks, worker job checks, lint results, package results or state contexts as a full-test substitute.
No part of local verification proves hosted event delivery, private-fork access, Actions scheduling or repository protection configuration.
