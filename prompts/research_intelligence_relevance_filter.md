# Research intelligence relevance filter

Given project context and normalized external results, return JSON only:

```json
{"selected":[{"source_type":"paper|github","source_url":"...","title":"...","summary":"...","relevance_reason":"...","related_project_issue":"...","confidence":0.0,"recommended_action":"shortlist|read|validate"}],"discarded_count":0,"selection_summary":"..."}
```

Select only genuinely relevant new items, up to the configured maximum. Confidence means project relevance, not truth probability. Do not create knowledge, promote candidates, or fabricate evidence.
