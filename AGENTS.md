# AGENTS.md

# Interacting with the Developer

## Chat Communication

Be as brief as possible while preserving meaning. Sacrifice grammatical completeness when useful; fragments are acceptable. Omit filler, repetition, and unnecessary explanation. Retain essential reasoning, uncertainty, and information needed for decisions. Expand when asked.

This instruction applies to chat responses, not code, documentation, or other deliverables.

# Architecture

## Purpose

Build software that is understandable, verifiable, and easy to change.

Follow these principles in proportion to the scope and risk of the task. Prefer the smallest sound solution supported by current requirements and evidence.

## Design for Change

Judge architecture by whether likely changes remain local, understandable, and easy to verify.

- Identify the contracts, boundaries, and sources of change relevant to the task.
- Introduce boundaries when they isolate a demonstrated concern, dependency, or source of change.
- Keep business policy independent of infrastructure details where that separation enables useful independent change or testing.
- Prefer explicit dependencies, cohesive components, and narrow interfaces.
- Avoid speculative abstractions. Generalize when demonstrated requirements justify it.
- Keep consequential decisions reversible where practical. Record the reasoning behind decisions that are expensive to reverse.

Do not add layers solely to conform to an architectural pattern.

## Discover Requirements and Define Sufficient Quality

Define success in terms of user outcomes and concrete examples.

- Identify the problem being solved and how the result will be evaluated.
- Distinguish actual constraints from assumptions and familiar conventions.
- Use working software, examples, and feedback to refine requirements.
- Establish relevant quality targets, such as correctness, latency, accessibility, reliability, and maintainability.
- Resolve tradeoffs using existing requirements and user priorities. Seek direction when those do not settle a consequential choice.
- Stop polishing when agreed outcomes and quality targets are met.

Do not interpret sufficient quality as permission to ignore known contract violations or unmet requirements.

## Use the Language of the Domain

Use consistent names from the problem domain across code, contracts, tests, and documentation.

- Prefer names that express intent and business meaning.
- Distinguish concepts that have different rules, even when their current representations look similar.
- Maintain a small shared glossary when terminology is ambiguous or specialized.
- Translate external terminology at integration boundaries where necessary.
- Rename concepts when their meaning changes, updating affected references together.

Avoid generic names that conceal responsibilities or require readers to reconstruct meaning from implementation details.

## Program by Contract

Make responsibilities explicit at meaningful component boundaries.

- Define accepted inputs, outputs, errors, and observable side effects.
- Callers must satisfy preconditions.
- Implementations must guarantee postconditions and preserve invariants.
- Substitutable implementations must not strengthen preconditions or weaken postconditions.
- Validate untrusted input at system boundaries. Handle expected invalid input through explicit error behavior.
- Use assertions or equivalent checks to expose internal contract violations. Assertions must not replace required runtime validation.
- Do not silently repair, reinterpret, or suppress a contract violation unless recovery is part of the contract.
- When changing a contract, update affected callers, implementations, tests, and documentation together.

Express contracts through types, schemas, executable checks, tests, and concise documentation. Use the lightest combination that makes obligations clear and violations observable.

## Fail Safely

When an invariant fails, stop the affected operation before invalid state propagates or further side effects occur.

- Distinguish expected operational failures from internal programming defects.
- Define recovery boundaries appropriate to the failure: an operation, transaction, request, worker, or process.
- Continue only when the relevant invariants remain intact or have been restored.
- Preserve useful diagnostic context without exposing sensitive data.
- Make partial completion explicit when an operation cannot be atomic.
- Do not catch an error merely to log it and continue as though the operation succeeded.

Terminate the process when continued execution cannot be trusted. Contain failures more narrowly when isolation and recovery are sound.

## Own Resources and Complete Their Lifecycles

Give every acquired resource an explicit owner responsible for releasing it or transferring ownership.

- Cover files, connections, locks, transactions, temporary artifacts, subscriptions, and background tasks.
- Use scoped cleanup mechanisms where available.
- Ensure cleanup on success, failure, early return, and cancellation.
- Keep resource lifetimes and mutable-state scopes as narrow as practical.
- Make ownership transfers visible in interfaces.
- Ensure background work has a defined completion, cancellation, and shutdown policy.
- Preserve the original failure when cleanup also fails, while making both diagnosable.

Do not rely on incidental process termination or garbage collection to release resources that require timely cleanup.

## Build Tracer Bullets

For a new capability or significant integration change, establish the smallest working path through the important system boundaries early.

A tracer bullet must:

- Exercise the architectural assumptions that matter.
- Connect real components where practical.
- Produce an observable result.
- Be verifiable through a repeatable check.
- Meet the engineering standards appropriate to its narrow scope.
- Provide a foundation that can grow incrementally.

Extend the working path in small steps, validating each addition. Avoid completing entire layers before discovering whether they work together.

Distinguish tracer bullets from prototypes:

- A tracer bullet establishes a narrow production path intended to evolve.
- A prototype answers a specific question and may be discarded.

State which approach you are taking when the distinction matters. Do not let disposable prototype shortcuts become production assumptions without review.

## Preserve Orthogonality

Keep independent concerns independently changeable.

- Make dependencies and data flow explicit.
- Minimize shared mutable state and hidden reliance on global context.
- Keep side effects visible at the relevant interface.
- Contain external service, platform, and vendor assumptions at appropriate boundaries.
- Avoid forcing consumers to depend on capabilities they do not need.
- Maintain one authoritative representation of each piece of knowledge; derive other representations where practical.

Similar syntax does not necessarily represent the same knowledge. Share code when it expresses the same rule and should change for the same reason.

Treat widespread edits for a supposedly local change as a signal to investigate coupling. A deliberate shared-contract change may legitimately affect many callers.

## Prefer Plain Text

Prefer durable, searchable, diffable plain text for human-maintained configuration, documentation, schemas, fixtures, and data.

- Use established formats and deterministic formatting.
- Keep authoritative source reviewable.
- Make generated representations reproducible where practical.
- Avoid manual edits to generated output.

Use binary, compressed, or specialized storage when text would be unwieldy in size, processing cost, access patterns, or fidelity. Media and large datasets are ordinary examples.

Commit generated artifacts when distribution, tooling, or consumer needs justify them. Document their source and regeneration process. Provide text-based inspection or export when it would materially aid maintenance.

## Do Not Program by Coincidence

Understand why behavior works before depending on it.

- Distinguish documented guarantees from accidental implementation behavior.
- Verify relevant assumptions through authoritative documentation, source inspection, or targeted experiments.
- Make ordering, timing, state, and environmental dependencies explicit.
- Check meaningful boundary conditions and failure paths.
- Understand an existing pattern's assumptions before copying it.
- Capture the intended rule in a test when regression is a meaningful risk.

When debugging, form a causal hypothesis and run a test that can distinguish it from plausible alternatives. Do not accumulate speculative fixes. Remove unsuccessful experimental changes unless they have an independently justified purpose.

A passing test demonstrates observed behavior under tested conditions. It does not explain the mechanism or establish correctness outside those conditions.

## Use Tests as Design Feedback

Treat tests as early consumers of the interfaces being designed.

- Use difficult setup, excessive mocking, or dependence on private details as signals to examine coupling and responsibilities.
- Test observable behavior, contracts, and invariants.
- Reproduce a bug with a failing test before fixing it where practical. Confirm that the test passes after the fix.
- Exercise significant states, transitions, and boundary conditions; line coverage alone is insufficient.
- Use property-based tests when invariants apply across a broad input space.
- Keep tests deterministic and sufficiently independent to make failures interpretable.

Improve a difficult interface when evidence supports doing so. Do not distort production design merely to accommodate a particular testing tool.

## Refactor Incrementally

Repair relevant design problems as understanding improves.

- Refactor when it reduces demonstrated complexity, duplication of knowledge, or obstacles to the current task.
- Keep behavior-preserving changes distinguishable from intentional behavioral changes.
- Establish suitable behavioral checks before restructuring unfamiliar code.
- Work in small steps and verify that intended behavior is preserved.
- Avoid broad cleanup that obscures the requested change.
- Record consequential defects outside the task's scope, including their impact, without automatically expanding the work.

Do not preserve a harmful design solely because it already exists. Do not replace a working design solely because another style is preferred.

## Do Not Outrun Your Headlights

Keep the size of each step within what available evidence allows you to reason about and verify.

- Inspect existing behavior before changing it.
- Work in small, coherent, reversible increments.
- Verify foundational assumptions before building further work on them.
- Run focused checks early.
- Reduce step size when uncertainty or consequences increase.
- Keep unrelated improvements outside the task unless they are necessary to complete it.
- Distinguish verified facts, assumptions, and unresolved questions when they affect decisions.

When visibility is insufficient, take a bounded investigative step: inspect code, consult documentation, add instrumentation, run an experiment, or build a prototype.

Seek user direction when an unresolved choice requires a product decision, additional authority, or acceptance of a material tradeoff that existing instructions do not settle. Resolve routine implementation choices independently.

## Working Method

### Before Changing Code

- Read applicable repository guidance and inspect the relevant code and tests.
- Check the working tree and preserve unrelated changes.
- Identify the intended outcome, quality targets, affected contracts, and task boundaries.
- Choose the smallest useful implementation or experiment.
- Identify how its result will be verified.

Keep planning proportional to the task. A small, understood fix does not require a separate design document.

### While Implementing

- Keep changes focused and maintain a runnable or testable state where practical.
- Validate uncertain integration assumptions early.
- Make errors and important state transitions observable.
- Automate repeated procedures when doing so reduces effort or error.
- Update affected tests and documentation alongside behavior.
- Remove superseded paths when compatibility and migration requirements permit.
- Reassess the approach if complexity, coupling, or uncertainty grows unexpectedly.

### Before Finishing

- Verify changed behavior and affected contracts in proportion to risk.
- Use integration or end-to-end checks when cross-component assumptions change.
- Cover meaningful failure paths, resource cleanup, and boundary cases.
- For nonbehavioral changes, use appropriate inspection or static checks.
- Review the final diff for unintended behavior, accidental coupling, stale documentation, and unrelated edits.
- Confirm that the intended outcome and relevant quality targets are met.
- Report the result, verification performed, and material limitations.

Never claim a check passed unless it ran successfully. If verification is blocked, state what remains unverified and why.

## When to Reassess

Reduce scope or investigate before extending the implementation when:

- Observed behavior has no adequate causal explanation.
- Success depends on unexplained timing, ordering, or environmental conditions.
- A local change repeatedly crosses unrelated boundaries.
- Further work would compound unverified assumptions.
- The design becomes more general than demonstrated requirements.
- Verification cannot distinguish success from silent failure.
- A consequential data change lacks an appropriate recovery strategy.
- Resource ownership or the validity of state after failure is unclear.

Resume implementation when evidence supports the next step. Escalate only when the remaining issue requires user judgment or authority.
