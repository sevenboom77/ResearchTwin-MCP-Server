# Demo Flow

This is a competition-demo story for showing that ResearchTwin is a long-horizon research agent, not a one-turn paper Q&A bot. It uses fictional data only.

## What the audience should see

By the end of the demo, the audience should see five different capabilities working together:

1. RAG helps the agent understand a research document.
2. The agent decides that completed work should be persisted.
3. A structured advisor requirement is retained for later use.
4. The agent reads current project state before planning.
5. A meeting-ready Markdown report is generated from stored evidence.

~~~mermaid
sequenceDiagram
    participant R as Researcher
    participant A as OpenTrek ResearchTwin Agent
    participant K as ResearchTwin_Docs RAG
    participant M as ResearchTwin MCP Server
    R->>A: Ask about fictional RNN-PPO methods
    A->>K: Retrieve relevant research material
    K-->>A: Grounded context
    A-->>R: Explain method and caveat
    R->>A: Report completed experiment and instability
    A->>M: record_research_activity
    M-->>A: Persisted UUID record
    R->>A: Provide fictional advisor requirement
    A->>M: record_advisor_instruction
    M-->>A: Persisted instruction
    A->>M: get_project_status
    M-->>A: Current stage, risks, and tasks
    R->>A: Request group-meeting report
    A->>M: generate_research_report
    M-->>A: Markdown report and saved path
    A-->>R: Evidence-based summary
~~~

## Before the demo

1. Use a clean, fictional runtime directory or temporary environment. Do not use a real student's research history, advisor messages, papers, or VPN details.
2. Start the primary Streamable HTTP server:

~~~powershell
Set-Location C:\work\ResearchTwin-MCP-Server
.\.venv\Scripts\Activate.ps1
.\scripts\start_server.ps1 -Transport streamable-http
~~~

3. In OpenTrek, register the server through the UI with STREAMABLE selected and the LAN_IPV4 /mcp URL.
4. Confirm the six MCP tools are visible before presenting.
5. Keep examples/sample_data/ open as a reference. It is illustrative input, not a directory that the server imports automatically.

The sample records describe a fictional project called Adaptive ResearchTwin Demonstrator. They contain RAG, RNN-PPO, VPN-routing, and report examples without a real person or institution.

## Suggested six-minute script

### Scene 1 — RAG gives research context

**Researcher says:**

> Based on the fictional RNN-PPO methods note in my knowledge base, why might a recurrent policy look unstable on long trajectories?

**What the agent does:**

The agent uses ResearchTwin_Docs RAG to retrieve the relevant fictional research material and provides a grounded explanation. It can mention uncertainty, evaluation conditions, and potential follow-up experiments.

**What to say to the audience:**

“This first step is retrieval and reasoning. It explains existing material, but it does not modify our project history.”

Do not call a persistence tool just because RAG answered a question.

### Scene 2 — The agent records an actual research activity

**Researcher says:**

> Today I completed the fictional RNN-PPO stability sweep. One setup reduced loss variance, but long-horizon returns are still unstable. Tomorrow I will repeat it with fixed seeds.

**Agent tool choice:** record_research_activity

~~~json
{
  "date": "2026-08-20",
  "activity_type": "experiment",
  "title": "RNN-PPO stability sweep",
  "description": "Ran a fictional recurrent-policy comparison using anonymized demo inputs.",
  "result": "One setup reduced loss variance.",
  "problem": "Long-horizon returns are still unstable.",
  "next_step": "Repeat the comparison with a fixed seed set.",
  "tags": ["rnn-ppo", "stability", "demo"],
  "source": "experiment"
}
~~~

**Expected evidence:**

The MCP result contains status success, a generated activity_id, and the persisted record. The agent can summarize the saved ID without exposing internal storage mechanics to the researcher.

**What to say to the audience:**

“The agent inferred that this is completed work with a result, a blocker, and a next action. It selected a state-changing tool rather than merely replying.”

### Scene 3 — The agent retains an advisor requirement

**Researcher says:**

> In our fictional group meeting, the advisor asked us to prioritize generalization evidence this week and compare held-out scenario performance by Saturday.

**Agent tool choice:** record_advisor_instruction

~~~json
{
  "instruction": "Prioritize generalization evidence in this week's update.",
  "task": "Compare held-out scenario performance.",
  "priority": "high",
  "deadline": "2026-08-23",
  "constraints": ["Use only anonymized demo data"],
  "follow_up": "Confirm the evaluation split at the next fictional meeting.",
  "source_note": "Fictional advisor group meeting"
}
~~~

**Expected evidence:**

The MCP response returns an instruction_id and structured record. The report generator can now include this requirement later without relying on the agent to remember a prior turn.

**What to say to the audience:**

“The server is not pretending to understand an unstructured chat log. The agent extracts the structured task, priority, deadline, and constraint, then persists only those fields.”

### Scene 4 — The agent checks project state before planning

**Researcher says:**

> Where are we now, and what risks should we address before the group meeting?

**Agent tool choice:** get_project_status

**Expected evidence:**

The returned project_status contains the current stage, completed tasks, pending tasks, risks, and important decisions. In the fictional fixture, the stage is MCP Tool Development and a VPN route is listed as an integration risk.

**Optional setup action:** If the status has not yet been initialized in the demo environment, have the agent call update_project_status first:

~~~json
{
  "project_name": "Adaptive ResearchTwin Demonstrator",
  "current_stage": "MCP Tool Development",
  "completed_tasks": ["Built fictional RAG knowledge-base fixture"],
  "pending_tasks": ["Prepare the fictional group-meeting report"],
  "risks": ["A VPN route can delay LAN requests during integration"],
  "important_decisions": ["Use Streamable HTTP as the primary transport"],
  "merge_mode": "merge"
}
~~~

**What to say to the audience:**

“The agent checks persisted state before making recommendations. This lets it distinguish known risks from a guess based only on the latest message.”

### Scene 5 — Generate a group-meeting report

**Researcher says:**

> Please generate this week's fictional group-meeting report.

**Agent tool choice:** generate_research_report

~~~json
{
  "start_date": "2026-08-17",
  "end_date": "2026-08-23",
  "report_type": "meeting",
  "project_name": "Adaptive ResearchTwin Demonstrator"
}
~~~

**Expected evidence:**

The response has:

- a Markdown report in the report field;
- a report_path under reports/;
- all eight fixed report sections;
- entries grounded in stored activities, status, and advisor requirements.

Open the returned Markdown or compare it with [the fictional sample report](../examples/sample_data/weekly_report_2026-08-17_to_2026-08-23.md). Highlight:

1. the RAG knowledge-base construction activity;
2. the fictional RNN-PPO reading and stability experiment;
3. the VPN Pending/recovery observation as an integration risk;
4. the advisor's generalization requirement;
5. the next-step plan.

**What to say to the audience:**

“This is not a generic prompt-generated weekly report. Its sections were assembled from durable project state, and the report was saved for later review.”

## Recommended narration

Use this one-sentence contrast near the end:

> RAG tells ResearchTwin what existing research material says; MCP lets ResearchTwin maintain what this research project has actually done and what it needs to do next.

Then point out the durable chain:

~~~text
RAG context → agent decision → persisted activity/instruction/status → generated report
~~~

## Evidence checklist

Before presenting, confirm:

- all six tools are discovered in OpenTrek;
- the Streamable URL uses /mcp, not an inferred endpoint;
- activity persistence can be proven with list_research_activities;
- the report has eight sections even if a category has no data;
- no real research content, private advisor message, credential, or actual VPN detail appears in the demo;
- runtime_data/ is not staged for Git.

## If live networking fails

Do not spend the entire presentation debugging a network. Show the local smoke-test evidence and use the fictional sample JSON and Markdown to demonstrate the data lifecycle. For a live LAN retry, follow [OpenTrek integration](open_trek_integration.md): check the listener, LAN IPv4, VPN/routing, and firewall in that order. Never automatically disable a VPN or modify firewall rules during the demo.
