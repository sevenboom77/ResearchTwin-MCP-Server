# Research intelligence search plan

You are planning a scheduled ResearchTwin discovery run. Read the supplied project context first. Return JSON only:

```json
{"queries":[{"query":"...","reason":"...","related_project_issue":"..."}]}
```

Return at most three focused queries. Each query must target a current stage, pending task, risk, or advisor instruction. Do not invent context, fabricate results, or decide Project Knowledge.
