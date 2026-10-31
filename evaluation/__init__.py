"""LLM-as-Judge evaluation framework.

    rubric.py       scoring rubric (accuracy / completeness / groundedness / separation)
    judge.py        LLM judge with JSON parsing, majority voting and a deterministic fallback
    metrics.py      retrieval + citation metrics and ``judge_answer`` (used by run_eval_multi_agent.py)
    stats.py        summaries, bootstrap CIs, paired permutation tests, Holm correction
    baselines.py    configuration A (no context) and B (code snapshot only)
    llm.py          judge / answer model resolution (shared rag.llm, Gemini, OpenAI-compatible, mock)
    spot_check.py   manual spot-check export and judge-vs-human agreement (Cohen's kappa)
"""
