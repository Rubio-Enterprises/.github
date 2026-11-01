# Triage labels

Keep exactly one canonical state label on a normal work issue. Labels are the
sole state store. Use `needs-triage` for unassessed issues or ones you cannot
make sense of; `in-progress` means work is underway.

## Assess readiness

Answer in order:

1. **Is the path settled?** Use `needs-refinement` when facts, analysis, or design
   remain open: no chosen approach with checkable acceptance criteria. A bug
   without durable reproduction evidence needs refinement. If analysis itself is
   the deliverable, judge it by question 2 instead. Planning together is
   refinement; how refinement is done is a working style, not a state.
2. **Once settled, who executes it?** Propose:
   - `delegate-to-agent`: unattended agent; one bounded outcome, acceptance
     criteria checkable from the PR, no human-only step.
   - `interactive-session`: person with an agent; execution is expected to hit
     unknowns needing a person in the loop.
   - `human-required`: person alone.

Use **execution** for carrying out work by anyone; reserve **implementation**
for code changes.

When a skill says `ready-for-agent`, apply both questions: unresolved work needs
`needs-refinement`; settled work takes the appropriate execution state above.
A skill's `needs-info` means `needs-refinement`, not a second label.
Apply the same execution distinction to `ready-for-human`.

A person approves assessed-work classification. Native label suggestions only
add: manually remove the old state when approving the new one. The audit reports
conflicts without correcting them. `agent:go` remains separate execution
authorization; `delegate-to-agent` is suitability, not permission to start.

## Record blockers

Keep the state the issue would have if unblocked. Use native "blocked by" links;
a dependency outside the organization gets a small tracking issue linked the
same way. Retain `## Blocked by` sections as readable evidence. Find blocked
issues with `is:blocked`, not a state label.

`documentation` is the sole optional common extra. Work kind uses native types
`bug`, `enhancement`, and `task`; Refactoring also uses `task`.
`wontfix` is not a common label; closure is a separate reviewed decision.
See [the registry, extension scopes, and read-only audit](https://github.com/Rubio-Enterprises/standards/blob/main/docs/issue-labels.md).
