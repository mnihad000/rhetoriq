# Codex workflow for RhetoriQ

Use GPT-5.6 Sol High as the primary agent for architecture, planning, ambiguous decisions, complex debugging, integration, and final review. The project-local Codex configuration selects Sol High as the main model and Luna High as the subagent default. Runtime or user-selected model and permission overrides may take precedence.

If the user invokes `$subagentic-workflow` or explicitly asks for the subagentic workflow, subagents, multi-agent mode, Luna workers, or delegated implementation, use [.agents/skills/subagentic-workflow/SKILL.md](.agents/skills/subagentic-workflow/SKILL.md) without asking again.

Otherwise, handle small, tightly scoped, or tightly coupled work directly in Sol. For larger work where independent Luna workers would materially improve speed or efficiency, ask: "This task is a good candidate for the Sol → Luna subagentic workflow. Do you want me to use subagents, or handle it entirely in Sol?" Continue useful independent investigation while awaiting the choice, but do not delegate until the user opts in. Do not ask this for trivial tasks.
