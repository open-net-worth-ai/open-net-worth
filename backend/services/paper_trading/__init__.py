"""Paper trading — a separate simulated book: pretend money, real market prices, never an order.

Only four callers may import this package (an import-linter contract): its API router, its
Lucius tools, the Automations service and the scheduler. It imports no real write choke, so
nothing here can touch an account, a transaction, a position, a lot or net worth (ADR
``2026-11-15-paper-trading-is-a-simulated-book-never-an-order-path``).
"""
