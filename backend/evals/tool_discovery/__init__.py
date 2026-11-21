"""Tool-discovery replay — does a model load a tool it needs, or tell the user it can't?

A live, opt-in check (it spends the chosen model's quota; never part of ``make check``). It
replays one real report against a throwaway book: a £1,250 Acme → Birch move booked as a
transfer the wrong way round plus a duplicate expense. The fix needs ``delete_transactions``
or ``merge_transactions``, neither of which is in the core tool offer — the model has to load
one with ``find_tools``. gpt-6-luna instead answered "I can't make the ledger changes from
this session" (2026-11-21).

It runs each model twice, with and without its model-family guidance
(``ai/model_guidance.py``), and scores the resulting LEDGER, not the reply.

    python3 -m backend.evals.tool_discovery --model codex_cli:gpt-6-luna --repeats 3
"""
