"""Memory benchmark — does this model do Lucius's memory well enough to ship?

Drives the REAL memory passes (extract, sort, import, tidy) and a real chat turn against
synthetic data in a throwaway data root, then scores the RESULTING STATE — never the model's
prose. A safety check (an identifier stored, a sensitive topic stored with the setting off, a
fact's text rewritten by the sort) fails the run on a single miss; quality checks feed a score.

Entry point: ``python3 -m backend.evals.memory --mock`` (zero cost) or ``make memory-eval``
(live, spends the chosen model's quota). See ``docs/ai/MEMORY_EVAL.md``.

This package is importable without touching the backend: only ``bootstrap`` + ``runner`` do,
and ``__main__`` isolates the data root before either is imported.
"""
