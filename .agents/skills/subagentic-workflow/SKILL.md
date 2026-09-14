---
name: subagentic-workflow
description: Run RhetoriQ coding work with Sol as orchestrator and bounded Luna implementation subagents when the user explicitly requests subagents, Luna workers, or this workflow.
---

# Sol and Luna workflow

Use this skill when the user invokes it or explicitly asks for the subagentic workflow. That request authorizes delegation for the task; do not ask again. Keep the primary agent on GPT-5.6 Sol High where the client allows it. Prefer the project-local `luna_implementer` agent or explicitly select GPT-5.6 Luna High for bounded implementation. Runtime model and permission controls may override project defaults.

## Sol: understand and divide

Personally inspect the relevant repository code, architecture, and conventions. Decide the technical approach and interfaces, identify dependencies, and make a concrete plan. Keep architecture, ambiguous decisions, complex debugging, security-sensitive choices, and cross-system integration with Sol.

Delegate only tasks with a clear implementation boundary. Each assignment should specify the goal, owned files or subsystem, expected behavior and interfaces, constraints, files the worker must not modify, checks to run, and what to report. Use at most three concurrent Luna workers, and fewer when that is more efficient. Run workers in parallel only when their file ownership and dependencies do not overlap; sequence coupled changes. Do not delegate merely to use workers.

## Luna: implement within scope

Each worker inspects relevant code, follows repository conventions, changes only its assigned scope, and runs relevant checks. It reports changed files, test/check results, assumptions, failures, and unresolved questions. If the assignment requires an architectural decision that Sol did not provide, the worker reports that need instead of choosing a new architecture.

## Sol: review and integrate

Wait for delegated work, inspect every worker diff, and compare the combined implementation with the plan. Verify interfaces between pieces, resolve conflicts and duplicated logic, and run relevant overall tests, build, typecheck, and lint. Investigate failures and make final fixes directly or assign a new, narrower task. Do not treat a worker's completion report as proof that the overall task is done. If a worker struggles or the work becomes substantially more complex, provide a precise follow-up or take over the difficult part.

In the final response, briefly state what Sol handled, what Luna handled, the worker count and whether they ran in parallel, any integration changes Sol made, and checks that passed or failed.
