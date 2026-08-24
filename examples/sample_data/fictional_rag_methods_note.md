# Fictional RNN-PPO Methods Note

**Demo-only notice:** This is an invented, anonymized teaching artifact. It is not a paper, dataset, result, or claim from a real research group.

## Scenario

The fictional Adaptive ResearchTwin Demonstrator uses a recurrent policy with a PPO-style update loop in a simulated sequential decision task. The evaluation compares short trajectories with deliberately harder long trajectories.

## Observation to discuss with RAG

The fictional note proposes that apparently unstable long-horizon returns can have multiple non-exclusive causes:

- hidden-state handling may differ between training and evaluation;
- the evaluation seed set may be too small to separate variance from a real regression;
- sequence batching or truncation choices may hide credit-assignment problems;
- one configuration may reduce loss variance without proving broader generalization.

## Demonstration question

Ask the RAG-enabled agent:

> Based on this fictional note, which checks should we run before claiming that a recurrent-policy change improved long-horizon stability?

The agent should explain the note and distinguish hypotheses from confirmed results. It should not create an MCP activity until the researcher reports actual work.
