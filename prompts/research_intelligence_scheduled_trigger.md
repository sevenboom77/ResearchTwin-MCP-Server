# Scheduled ResearchTwin trigger

Run one scheduled ResearchTwin intelligence cycle: call `get_research_context`, plan up to three project-focused queries, call `search_external_research`, filter for genuinely relevant new items, record each retained item with status `discovered`, compose a brief, and call `record_research_intelligence_brief` with `trigger_type=scheduled`. Never promote candidates, prepare knowledge, sync Bailian, or send notifications. If no item is relevant, persist a short empty brief. Do not invent facts.
