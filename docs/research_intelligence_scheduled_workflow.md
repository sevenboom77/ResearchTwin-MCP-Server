# ResearchTwin Research Intelligence Scheduler

This document is a hand-build blueprint for a Bailian Scheduled Workflow. It is not a generated DSL and does not create or configure cloud resources.

## Why Workflow

Routine scheduled intelligence has a fixed trigger, quality gate, persistence sequence, and bounded retries. Ordinary conversations remain the OpenTrek Agent's autonomous plan and continue using RAG/MCP directly.

## Nodes and mappings

1. **Start / scheduled trigger**: `project_name=ResearchTwin`, `brief_type=daily`, `limit_per_source=5`, `max_candidates=3`.
2. **MCP_get_research_context**: pass project name and bounded activity/advisor/candidate/brief/knowledge limits.
3. **LLM_search_plan**: use `prompts/research_intelligence_search_plan.md`; output up to three query objects.
4. **Iterator_queries**: iterate query objects.
5. **MCP_search_external_research**: sources `arxiv,github`, bounded limit, sort `recent` or `relevance`.
6. **Aggregate_external_results**: technical dedupe by arXiv source_id or GitHub source_id; no semantic merge.
7. **LLM_relevance_filter**: use `prompts/research_intelligence_relevance_filter.md`; select at most max_candidates.
8. **Condition_has_selected**: empty selection skips candidate iteration but still permits a short empty brief.
9. **Iterator_selected**: iterate each selected external item.
10. **MCP_record_candidate_intelligence**: persist each relevant item as `discovered`; return `candidate_id` or a handled `duplicate_candidate` result.
11. **Aggregate_candidate_records**: collect only newly created IDs; retain selected title/URL for duplicate references.
12. **LLM_compose_brief**: use `prompts/research_intelligence_brief_compose.md`.
13. **MCP_record_research_intelligence_brief**: map project, dates, title, summary, markdown, candidate IDs, search queries, and `trigger_type=scheduled`.
14. **End/output**: return brief Markdown, brief ID, and candidate count.

When `Condition_has_selected=false`, branch directly to `LLM_compose_empty_brief` (or reuse `LLM_compose_brief` with an empty-selection input), then to node 13 and End. The empty branch must never fabricate findings.

Candidate and brief data are persisted through MCP/NAS-backed storage. Project Knowledge still requires a separate promoted candidate, local prepare, and explicit user-confirmed Bailian sync. External Search Result != Candidate != Intelligence Brief != Project Knowledge != Project Decision.

## Failure and retry

Bound each iterator and LLM retry. A source failure is recorded while other sources continue. Prefer `recent_candidates` in the relevance filter to avoid duplicates. If the candidate MCP node returns `duplicate_candidate`, continue the run, do not create a duplicate, omit a new ID, and retain the selected title/URL as a historical reference. If the UI cannot branch on MCP `isError`, document this fallback as an implementation detail for console testing; do not claim duplicate is ordinary success. A validation or persistence error is surfaced with the node name; never auto-promote or auto-sync. Retry the whole scheduled run only through the platform scheduler, with daily duplicate protection enforced by `record_research_intelligence_brief`.

## Console build order and run-once demo

Create nodes in the numbered order above, map outputs explicitly, run once with a temporary project, inspect Candidate and Brief records, then enable a daily trigger. Configure timezone and dates in the Bailian console's native scheduler controls; do not invent variable names here. No DSL is supplied by this repository.
