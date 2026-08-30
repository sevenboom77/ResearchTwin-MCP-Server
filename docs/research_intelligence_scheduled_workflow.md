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
9. **Iterator_selected**: call `record_candidate_intelligence` with status `discovered`; duplicate_candidate is a handled no-op branch.
10. **LLM_compose_brief**: use `prompts/research_intelligence_brief_compose.md`.
11. **MCP_record_research_intelligence_brief**: map project, dates, title, summary, markdown, candidate IDs, search queries, and `trigger_type=scheduled`.
12. **End/output**: return brief Markdown and IDs.

Candidate and brief data are persisted through MCP/NAS-backed storage. Project Knowledge still requires a separate promoted candidate, local prepare, and explicit user-confirmed Bailian sync.

## Failure and retry

Bound each iterator and LLM retry. A source failure is recorded while other sources continue. A duplicate candidate is skipped. A validation or MCP persistence error is surfaced with the node name; never auto-promote or auto-sync. Retry the whole scheduled run only through the platform scheduler, with daily duplicate protection enforced by `record_research_intelligence_brief`.

## Console build order and run-once demo

Create nodes in the numbered order above, map outputs explicitly, run once with a temporary project, inspect Candidate and Brief records, then enable a daily trigger. Configure timezone and dates in the Bailian console's native scheduler controls; do not invent variable names here. No DSL is supplied by this repository.
