# TradingAgents — findings on the pinned commit

Read on 2026-11-19 from a shallow clone of `github.com/TauricResearch/TradingAgents`.

- **Pinned commit:** `9fa1196fffbcfd9de5146d2dd2869df84ab99838` (2026-11-04, "announce Trading-R1 official release"), `tradingagents.__version__ == "0.7.1"`.
- Where this file and `trading-agents.md` disagree, this file wins (it is the source). Differences are listed last.

## Packaging

- `requires-python = ">=3.11"` (ONW runs 3.12, so `--python <sys.executable>` is fine).
- Dependencies: `cryptography>=42`, `langchain-core>=1.6.5`, `langchain-anthropic>=1.7.4`, `langchain-google-genai>=4.4.0`, `langchain-openai>=1.6.6`, `langgraph>=1.2.12`, `langgraph-checkpoint-sqlite>=3.1.1`, `markdown-it-py`, `packaging`, `pandas>=3.0.6`, `python-dotenv`, `pytz`, `questionary`, `requests>=2.32.4`, `rich`, `typer`, `stockstats`, `typing-extensions`, `yfinance>=1.7.0`. Optional extra `bedrock` (not needed). Pandas 3 and the Anthropic/Google langchain packages are installed even though we never use them (heavy venv; install timeout must allow it).
- Build backend setuptools; installable with `uv pip install git+https://github.com/TauricResearch/TradingAgents@<sha>`.
- Version check: `TRADINGAGENTS_NO_UPDATE_CHECK=1` exists (`cli/updates.py`, CLI only; harmless for the worker).

## PortfolioContext (exact schema, `tradingagents/portfolio.py`)

```python
class Position(BaseModel):          # ConfigDict(allow_inf_nan=False)
    ticker: str
    quantity: float                 # signed; negative = short
    average_price: float | None = None

class PortfolioContext(BaseModel):  # ConfigDict(allow_inf_nan=False)
    cash: float | None = None       # free cash
    currency: str | None = None     # ONE label for cash AND prices
    positions: list[Position] = []
```

- A single `currency`. Positions in another currency must be FX-converted by ONW (`disclosure.fx_converted`), as the spec anticipated.
- Three distinct states: `None` (no context), empty positions (flat book), populated. `ticker`-level runs pass `portfolio=None`; never an empty `PortfolioContext` for "no context" (it would read as flat).
- `propagate(..., portfolio=PortfolioContext|None)` renders it with `portfolio.render(ticker)` into the state key `portfolio_context` (a prompt string: current position, cash, other positions as `TICKER quantity` without prices). It only goes into prompts; data tools take ticker/date only (verified: `portfolio` appears in `trading_graph.py`, `propagation.py`, `agents/context.py` only).
- Matching of the analyzed ticker is via `normalize_symbol` (handles broker spellings).
- The run signature (checkpoint resume) includes `portfolio.fingerprint()`. Checkpointing is off by default (`checkpoint_enabled: False`); leave it off.

## LangGraph node names (`graph/setup.py`, `graph/analyst_execution.py`)

Top-level nodes (these are the `stage` values for progress and the keys for the role map):

| Node | Model tier |
|---|---|
| `Market Analyst`, `Sentiment Analyst`, `News Analyst`, `Fundamentals Analyst` (only the selected ones; keys `market`, `social`, `news`, `fundamentals`) | quick |
| `Memory Log` (only when memory node given — always, via `TradingAgentsGraph`) | quick (reflection calls) |
| `Bull Researcher`, `Bear Researcher` | quick |
| `Research Manager` | deep |
| `Trader` | quick |
| `Aggressive Analyst`, `Conservative Analyst`, `Neutral Analyst` | quick |
| `Portfolio Manager` | deep |

- There is **no** `Risk Judge` node; the spec's role map entry is dead. The Research Manager and Portfolio Manager are the two deep-tier nodes.
- **Analysts run in parallel**, together with `Memory Log`, all starting from `START`; the debate starts when every one has finished. Progress is not a linear sequence.
- Each analyst node is a **sub-graph** (`_analyst_graph`) with inner nodes `agent`, `tools`, `wrap_up` (`Sentiment Analyst` has no tools: `agent` only). So a LangChain callback's `metadata["langgraph_node"]` inside an analyst is `agent` / `tools` / `wrap_up`, **not** `Market Analyst`. The top-level name is in `metadata["langgraph_checkpoint_ns"]`, shaped `"Market Analyst:<task-id>|agent:<task-id>"` — take the first segment before `:`. For top-level nodes the checkpoint_ns is `"<Node>:<task-id>"` and `langgraph_node` is the node name.
- Tool nodes (inside analysts) therefore need no separate map entry: they inherit the analyst via checkpoint_ns.
- The `Memory Log` node settles past decisions: it can make reflection LLM calls (`Reflector` uses the quick LLM, one `invoke` per settled decision) and fetches prices. Map to role `memory`. With a fresh scratch memory dir it makes none.
- `progress` source: `TradingAgentsGraph.stream_run` exists but uses `stream_mode ["values","tasks"]` and is only used with `debug=True`. The spec's callback-handler approach (`callbacks=[h]` passed to the constructor, forwarded to both LLM clients via `create_tier_client(..., callbacks=...)`) works and is the supported hook. Node start/finish progress can come from the handler's `on_chain_start`/`on_chain_end` with `metadata["langgraph_node"]` for top-level nodes.

## Final state and return value

`propagate()` returns `(final_state, signal)`; `signal = run_rating(final_state)` = `final_state["final_rating"]` else regex-parsed from `final_trade_decision`, else `"REVIEW"`. **`run_rating` makes no LLM call.** `REVIEW` is the "unparseable" sentinel (`agents.rating.is_review`).

`final_state` keys (`agents/state.py`): `company_of_interest`, `asset_type`, `instrument_context`, `trade_date`, `market_report`, `sentiment_report`, `news_report`, `fundamentals_report`, `investment_debate_state{bull_history, bear_history, history, current_response, count}`, `investment_plan`, `trader_investment_plan`, `risk_debate_state{aggressive_history, conservative_history, neutral_history, history, latest_speaker, current_aggressive_response, current_conservative_response, current_neutral_response, count}`, `final_trade_decision`, `final_rating`, `past_context`, `memory_note`, `portfolio_context`, `messages`.

- **No `judge_decision`** in either debate state (spec §7.3 lists it as optional; it does not exist). The research verdict is `investment_plan`; the risk verdict is `final_trade_decision`.
- `final_state` also holds `messages` (non-serialisable LangChain objects) and `portfolio_context` (rendered amounts): the worker must send only the whitelisted keys, never dump the state.
- A report field from an unselected analyst may be missing or empty; read with `.get`.
- `propagate` also writes `full_states_log` under `results_dir/<ticker>/TradingAgentsStrategy_logs` and appends to the memory log (`memory_log_path`) — both under the scratch dirs we set. They contain portfolio-derived text (decision text, past context).
- `trade_date` must be canonical `YYYY-MM-DD` and not after `get_current_date()` (raises `ValueError`).

## LLM transport

- Provider key `openai_compatible` is registered (`OPENAI_COMPATIBLE_PROVIDERS`), `require_base_url=True`, `key_optional=True`, chat class `LocalCompatibleChatOpenAI`. Env var for its key is `OPENAI_COMPATIBLE_API_KEY` (read when set, else the placeholder `"EMPTY"`). It does NOT use the Responses API (`use_responses_api=False`), so it speaks plain `POST {base_url}/chat/completions`.
- Config keys: `llm_provider="openai_compatible"`, `backend_url="http://127.0.0.1:<port>/v1"`, `deep_think_llm`, `quick_think_llm`, `llm_max_retries` (int, forwarded as `max_retries`), `max_debate_rounds`, `max_risk_discuss_rounds` (NOT `max_risk_rounds`), `max_tool_rounds`, `max_recur_limit` (100; must exceed `2*max_tool_rounds+2`, so `max_tool_rounds` ≤ 20 is safe with the default 100), `output_language`, `data_vendors`, `results_dir`, `data_cache_dir`, `memory_log_path`, `checkpoint_enabled`. Tier-specific overrides exist (`quick_think_provider`, `deep_think_backend_url`, ...) but are not needed: one bridge URL serves both aliases.
- **Streaming: no.** Non-debug `propagate` uses `graph.invoke` and every agent calls `llm.invoke(...)`; `NormalizedChatOpenAI` is not constructed with `streaming=True` (only the ChatGPT-plan class is). The bridge needs `stream:false` only; SSE is not required for v1.
- **Structured output does NOT use `response_format`.** For `openai_compatible` with an unknown model name (`onw-deep`), `get_capabilities` returns `_DEFAULT` → `preferred_structured_method="function_calling"`, and `LocalCompatibleChatOpenAI` forces `tool_choice=None`. So Trader / Research Manager / Portfolio Manager send **`tools=[<one function named after the Pydantic schema>]` with no `tool_choice`** and parse `message.tool_calls`. If the model answers in plain text, TA retries once as free text (`invoke_structured` fallback). `response_format` translation in `openai_shape` is therefore optional (keep it as a robustness feature, not a requirement).
- Analysts send `tools=[...]` via `llm.bind_tools(tools)` and loop on `tool_calls`; tool results come back as `role: "tool"` messages with `tool_call_id`. `wrap_up` appends a `HumanMessage`.
- Prompts may be passed as a plain string (becomes one user message) or a list of messages.
- Each LangChain call to the endpoint is one HTTP request; the model name sent is exactly `deep_think_llm` / `quick_think_llm`. `LocalCompatibleChatOpenAI.warn_if_unknown_model` only logs a warning for unknown names.
- Deep tier is used by `Research Manager` and `Portfolio Manager`; every other node (and reflection) uses quick.

## Environment

- Data vendor keys read from env: `ALPHA_VANTAGE_API_KEY`, `FRED_API_KEY`; `SEC_EDGAR_USER_AGENT` (falls back to a package default). Default `data_vendors`: yfinance, `fundamental_data="sec_edgar,yfinance"`, `macro_data="fred"` (raises "not configured" without a key — the worker should set `macro_data` to `""`/a keyless choice when no FRED key; confirm in WP3), `prediction_markets="polymarket"`.
- Paths: `TRADINGAGENTS_RESULTS_DIR`, `TRADINGAGENTS_CACHE_DIR`, `TRADINGAGENTS_MEMORY_LOG_PATH` (read when `tradingagents.default_config` is imported; the worker may also set the config keys explicitly). `~/.tradingagents` is the default home — the worker must always override all three so nothing lands in the user's home.
- `TRADINGAGENTS_*` env overrides (`_ENV_OVERRIDES`) also exist; `scrubbed_environ()` should not pass any through.

## Differences from the spec

| Spec | Pinned source |
|---|---|
| Config key `max_risk_rounds` in the worker request | `max_risk_discuss_rounds` (ONW's own setting keeps the name `max_risk_rounds`; translate in the worker request) |
| Node `Risk Judge` | does not exist |
| `judge_decision` in debate states | does not exist |
| `langgraph_node` gives the role | only for top-level nodes; inside analysts it is `agent`/`tools`/`wrap_up` — use the first segment of `langgraph_checkpoint_ns` |
| Analysts → debate is a sequence | analysts and `Memory Log` run in parallel |
| `response_format` json_schema may be used | structured output is a single forced-free function tool without `tool_choice` |
| Streaming "verify" | not used |
| `run_rating` extra LLM call | none |
