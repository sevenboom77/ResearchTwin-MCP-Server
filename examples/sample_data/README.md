# Fictional and anonymous sample data

Everything in this directory is invented for demonstration. It contains no real student, advisor, institution, paper, chat record, network address, experiment result, or credential.

## Files

| File | Purpose |
| --- | --- |
| fictional_rag_methods_note.md | A short made-up methods note that can be used to illustrate the RAG side of a demo. |
| research_logs.json | Fictional activity records covering RAG setup, RNN-PPO reading/experiments, and VPN-routing diagnosis. |
| project_status.json | A fictional current project snapshot in the MCP Tool Development stage. |
| advisor_instructions.json | A fictional structured advisor requirement. |
| weekly_report_2026-08-17_to_2026-08-23.md | A meeting-style report grounded in the other fixtures. |

The JSON structures mirror the server's persisted data shape:

- research_logs.json wraps records in an activities array;
- advisor_instructions.json wraps records in an instructions array;
- project_status.json is the current project snapshot;
- reports are Markdown files under a reports/ directory at runtime, but this example stays here so it is visibly safe and easy to review.

## Safe use

These files are not automatically imported by the server. Use them as reference input for an isolated demo, or recreate their facts with MCP tool calls. Do not copy real runtime_data/ into this directory and do not replace these fictional values with personal research content before committing.
