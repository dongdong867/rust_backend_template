# Maintainer command security and validation

The only command forms are `/lint`, `/test` and `/test-{crate}`.
See [the README](../README.md#maintainer-requested-checks) for command selection and [Make](../Makefile) for local checks.
Results and ordinary logs are inspected in GitHub Actions, not in a bot comment or custom PR-result status.

## Acceptance and trust boundaries

`ci-command.yml` handles newly created issue comments.
Its read-only route job checks exact syntax, an open PR and the commenter's current repository write, maintain or admin permission.
Rejected comments never enter the acceptance lock or dispatch a worker.
The controller rechecks comment identity, body, PR association, permission and edit state before accepting it.

A `created` event can contain an already-edited body.
Routing and acceptance therefore query the comment's explicit GraphQL `lastEditedAt`, bound to the same node ID, database ID and body.
Only an explicit null edit time is accepted; missing, malformed, mismatched or unavailable metadata cannot authorize execution.
Comparing REST `created_at` and `updated_at` is insufficient because edits can happen within the same second.
The query uses variables rather than embedding comment-controlled values, and API diagnostics never include raw response bodies.
The GitHub-provided `GITHUB_GRAPHQL_URL` selects the GraphQL endpoint, including on Enterprise installations.
Edits after acceptance are not new requests and do not retarget the captured run.

An accepted request contains a PR number, command, comment ID, accepting controller run ID, exact PR head SHA and trusted worker workflow SHA.
The controller uses REST API version `2026-03-10` and binds ownership to the worker ID returned by dispatch.
A legacy dispatch response without an ID fails closed; ownership is never inferred by listing matching runs.
Only the controller has `actions: write` for dispatch and cancellation.
Its `statuses: write` permission maintains internal ownership records on the root commit, not results on PR heads.
It checks out trusted `github.sha` and never evaluates PR source, Makefiles, toolchain files, caches or artifacts.

Workers have read-only permissions and non-persisted checkout credentials.
No production secret, writable token, privileged environment, self-hosted runner or shared writable cache is used for PR execution.
Do not enable private-fork settings that send write tokens or secrets to PR workflows.
An authorized command explicitly approves execution of the captured code; fork-workflow approval settings do not replace this authorization.

Workers validate their bound ID and durable request before execution admission and again before checking out PR code.
They fetch the captured SHA through the base repository, verify the checkout SHA and run only the requested Make targets.
Tool-pin discovery and all other PR evaluation happen only after the read-only boundary.
The workflow definition and validation code come from the trusted default branch.
Unsupported reruns, duplicate dispatches, superseded requests, changed heads and mismatched workflow identities cannot execute as the accepted worker.
Create a new command comment instead of manually dispatching or rerunning jobs.

## Ownership and cancellation

GitHub concurrency does not guarantee dispatch ordering.
Acceptance, bookkeeping and cancellation retain one non-cancelling repository/command controller lock.
Workers have no cancelling concurrency group, so a delayed older worker cannot cancel a newer request.
Only the controller cancels specifically recorded obsolete worker IDs for the same PR and command.
Different commands, packages and PRs execute independently, including PRs sharing a head SHA.
The command hash preserves exact, case-sensitive package selectors.

The controller uses `queue: max`, preserving up to 100 pending jobs instead of replacing a single pending job.
Overflowed, manually cancelled or failed controller runs need a new command; they do not prove checks passed.
Commit statuses on the repository's single root commit hold `ci-command-state/<PR>/<command-hash>` cursors.
These are internal bookkeeping, not required checks or test results.
The comment ID orders accepted requests across head and default-branch updates.
Older controller events cannot replace a newer cursor.
Existing v2 and v3 records remain readable; v3 reason codes remain validated for compatibility.
An internal `pending` record means the latest request was accepted/registered, not that its worker is still running.
Worker completion does not finalize these cursors; run attempts and bound IDs still prevent unsupported re-execution.
Completed historical workers need no cancellation, and deleted obsolete runs do not block fresh requests.
Only Actions-authored records are accepted; corrupt or foreign records fail closed.
Repositories with multiple root commits are refused; do not rewrite history while runs are active.
GitHub's status-count limits still apply; bookkeeping API failures require inspection, not a success fallback.

## Actions-only results and rollout

Inspect **CI command worker** runs in Actions using the command and captured SHA in each run title.
The **Validate accepted request** and **Run PR command** jobs show actual results and ordinary logs.
Make failures stop further feature modes and propagate a nonzero result; unknown packages never fall back to workspace tests.
Cancellation and timeout remain nonpassing native Actions conclusions.
No completion workflow, bot result comment, log-summary download, test-count report or coverage comparison is part of this flow.
New requests publish no custom `ci-comment` result contexts.
Historical contexts may remain pending and are deliberately not rewritten or treated as passing results.
Do not configure them, controller jobs, worker jobs or internal cursor contexts as a full-test required-check gate.
Repository protection settings are not changed.

The controller, worker and execution helpers must reach the default branch before hosted QA can exercise this version.
Both generated variants retain the same execution support.
Hosted validation must cover exact syntax and authorization; edited comments before routing/acceptance; successful and failed lint/full/package execution; unknown packages; exact captured heads; cancellation; independent PRs/commands; duplicate/rerun rejection; and fork-head access.
Private-fork support needs its own environment and permission checks.
Local port tests and generation checks do not prove hosted token permissions, event delivery, scheduling or fork access.
Unperformed actor, fork or error checks remain unverified.

## Workflow checker compatibility

Pinned actionlint `1.7.12` does not recognize GitHub's documented `queue: max`.
`make ci-config-check` checks original workflows and validates the controller's one canonical non-cancelling control mapping.
Only the exact unsupported-key diagnostic at that validated location is exempted.
Other workflows, duplicate keys, wrong values, alternate placements and unrelated diagnostics still fail.
Remove the compatibility exception when a released checker supports the field.
