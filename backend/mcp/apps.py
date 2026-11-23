"""MCP Apps (extension ``io.modelcontextprotocol/ui``): Lucius's answer, and any approval he is
waiting on, shown as a card inside the app's chat (Claude, ChatGPT).

* ``talk_to_lucius`` declares the card (``_meta.ui.resourceUri``); its result carries the answer
  and the waiting approvals in ``structuredContent`` for the card, and each approval's one-time
  token in the result's ``_meta`` — which reaches the card, not the model's context.
* The card's buttons call ``answer_lucius_approval``, an app-only tool (``visibility: ["app"]``):
  the host hides it from the model and refuses it from anything but the card. It also needs the
  card's token, which the model never sees, so the model can't press the buttons even on a host
  that got visibility wrong. It runs exactly what tapping the card in Open Net Worth runs.

Apps without MCP Apps ignore all of this and read the text answer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from backend.core import mcp_ui
from backend.mcp.adapter import tool_allowed

TALK_TOOL = "talk_to_lucius"
ANSWER_TOOL = "answer_lucius_approval"
UI_URI = "ui://open-net-worth/lucius-card.html"
UI_MIME = "text/html;profile=mcp-app"
CARD_META_KEY = "_card_meta"
UI_TOOLS = frozenset({TALK_TOOL, ANSWER_TOOL})

_ANSWER_DESCRIPTOR = {
    "name": ANSWER_TOOL,
    "description": "Approve or decline a change Lucius is waiting on (used by the in-chat card).",
    "inputSchema": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "card_id": {"type": "string"},
            "card_token": {"type": "string"},
            "decision": {"type": "string", "enum": ["approve", "deny"]},
        },
        "required": ["conversation_id", "card_id", "card_token", "decision"],
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    "_meta": {"ui": {"resourceUri": UI_URI, "visibility": ["app"]}},
}


EXTENSION = "io.modelcontextprotocol/ui"

# The callers whose ``initialize`` declared MCP Apps support live in ``core.mcp_ui``. A caller
# that never declared it gets no card, no app-only tool and no card tokens: a client without MCP
# Apps ignores ``visibility`` and may put a result's ``_meta`` in front of its model, which would
# hand the model both the tool and the token.


def note_initialize(surface: str, params: dict) -> None:
    caps = params.get("capabilities") if isinstance(params, dict) else None
    extensions = caps.get("extensions") if isinstance(caps, dict) else None
    ui = extensions.get(EXTENSION) if isinstance(extensions, dict) else None
    mime_types = ui.get("mimeTypes") if isinstance(ui, dict) else None
    mcp_ui.note(surface, isinstance(mime_types, list) and UI_MIME in mime_types)


def supports_ui(surface: str) -> bool:
    return mcp_ui.supports(surface)


def can_talk(scopes: tuple[str, ...]) -> bool:
    return tool_allowed(TALK_TOOL, scopes)


def decorate(descriptors: list[dict], scopes: tuple[str, ...], surface: str) -> list[dict]:
    """Attach the card to ``talk_to_lucius`` and add the card's app-only answer tool."""
    if not can_talk(scopes) or not supports_ui(surface):
        return descriptors
    out = [
        {**d, "_meta": {"ui": {"resourceUri": UI_URI}}} if d["name"] == TALK_TOOL else d
        for d in descriptors
    ]
    return sorted([*out, _ANSWER_DESCRIPTOR], key=lambda d: d["name"])


def resource_descriptors(scopes: tuple[str, ...], surface: str) -> list[dict]:
    if not can_talk(scopes) or not supports_ui(surface):
        return []
    return [{"uri": UI_URI, "name": "Lucius", "mimeType": UI_MIME}]


def read_ui(scopes: tuple[str, ...], surface: str) -> Optional[list[dict]]:
    """The card's HTML, or ``None`` when this caller may not talk to Lucius or has no MCP Apps."""
    if not can_talk(scopes) or not supports_ui(surface):
        return None
    ui: dict[str, Any] = {"prefersBorder": True, "csp": {}}
    domain = _claude_domain()
    if domain:
        ui["domain"] = domain
    return [{"uri": UI_URI, "mimeType": UI_MIME, "text": CARD_HTML, "_meta": {"ui": ui}}]


def _claude_domain() -> str:
    """Claude serves a card from a per-server domain: the first 32 hex characters of the SHA-256
    of the server URL, under claudemcpcontent.com (Claude's MCP Apps docs)."""
    try:
        from backend.services.mcp_oauth.settings import public_url, resource_url

        if not public_url():
            return ""
        server_url = resource_url()
    except Exception:  # noqa: BLE001 — no public address: hosts pick their own
        return ""
    digest = hashlib.sha256(server_url.encode()).hexdigest()[:32]
    return f"{digest}.claudemcpcontent.com"


def shape(text: str, surface: str) -> dict:
    """A UI tool's ``CallToolResult``: the model reads ``content`` (no tokens), the card reads
    ``structuredContent`` and the tokens in ``_meta`` — tokens only for a caller that declared
    MCP Apps."""
    try:
        data = json.loads(text)
    except ValueError:
        return {"content": [{"type": "text", "text": text}], "isError": True}
    if not isinstance(data, dict):
        return {"content": [{"type": "text", "text": text}], "isError": False}
    meta = data.pop(CARD_META_KEY, None)
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        "structuredContent": data,
        "isError": "error" in data,
    }
    if meta and supports_ui(surface):
        result["_meta"] = meta
    return result


async def answer(arguments: dict, surface: str, scopes: tuple[str, ...]) -> dict:
    """The card's Approve / Decline. The same checks as ``talk_to_lucius`` (scope, the app's own
    conversation), plus the card's one-time token."""
    from backend.ai import app_asks
    from backend.ai.tools.talk_to_lucius import result_for
    from backend.core import mcp_progress
    from backend.core.errors import OpenNetWorthError

    if not can_talk(scopes) or not supports_ui(surface):
        return _error("This connection can't answer Lucius's approvals.")
    cid = str(arguments.get("conversation_id") or "")
    card_id = str(arguments.get("card_id") or "")
    decision = str(arguments.get("decision") or "")
    if not cid or not card_id:
        return _error("conversation_id and card_id required.")
    try:
        session_id = app_asks.open_conversation(surface, cid, "")
        token = str(arguments.get("card_token") or "")
        if not app_asks.card_token_ok(session_id, card_id, token):
            return _error("This card has expired. Approve it in Open Net Worth instead.")
        reply = await app_asks.answer_card(
            session_id, card_id, decision, surface, mcp_progress.report
        )
        app_asks.spend_card_token(session_id, card_id)
    except OpenNetWorthError as exc:
        return _error(exc.message or str(exc))
    waiting = app_asks.waiting_approvals(session_id)
    return shape(json.dumps(result_for(session_id, reply, waiting), ensure_ascii=False), surface)


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


CARD_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:light dark;font-family:var(--font-sans,system-ui,-apple-system,"Segoe UI",sans-serif)}
body{margin:0;padding:14px 16px;font-size:14px;line-height:1.45;background:transparent}
.who{font-size:12px;opacity:.65;margin-bottom:6px}
.answer{white-space:pre-wrap;margin:0}
.card{border:1px solid color-mix(in srgb,currentColor 18%,transparent);border-radius:10px;padding:12px;margin-top:12px}
.what{font-weight:600;margin-bottom:10px}
.row{display:flex;gap:8px}
button{font:inherit;padding:7px 14px;border-radius:8px;cursor:pointer;color:inherit;background:transparent;
border:1px solid color-mix(in srgb,currentColor 25%,transparent)}
button.primary{background:CanvasText;color:Canvas;border-color:CanvasText}
button:disabled{opacity:.5;cursor:default}
.note{font-size:13px;opacity:.75;margin-top:8px}
</style></head><body>
<div class="who">Lucius · Open Net Worth</div>
<div id="answer" class="answer"></div>
<div id="cards"></div>
<script>
(() => {
  let nextId = 1, state = {conversation_id: "", tokens: {}};
  const pending = new Map();
  const send = (m) => window.parent.postMessage(Object.assign({jsonrpc: "2.0"}, m), "*");
  const request = (method, params) => new Promise((resolve, reject) => {
    const id = nextId++; pending.set(id, {resolve, reject}); send({id, method, params});
  });
  const el = (tag, cls, text) => {
    const n = document.createElement(tag); if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text; return n;
  };
  function render(result) {
    const data = (result && result.structuredContent) || {};
    const meta = (result && result._meta && result._meta.card_tokens) || {};
    state = {conversation_id: data.conversation_id || state.conversation_id, tokens: meta};
    document.getElementById("answer").textContent = data.answer || data.error || "";
    const box = document.getElementById("cards"); box.textContent = "";
    (data.waiting || []).forEach((c) => {
      const card = el("div", "card"); card.appendChild(el("div", "what", c.what));
      if (!c.in_chat || !state.tokens[c.id]) {
        card.appendChild(el("div", "note", "Approve or decline this in Open Net Worth."));
        box.appendChild(card); return;
      }
      const row = el("div", "row");
      const ok = el("button", "primary", "Approve"), no = el("button", "", "Decline");
      ok.onclick = () => decide(c.id, "approve", card); no.onclick = () => decide(c.id, "deny", card);
      row.append(ok, no); card.appendChild(row); box.appendChild(card);
    });
  }
  async function decide(cardId, decision, card) {
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    const note = el("div", "note", decision === "approve" ? "Approving…" : "Declining…");
    card.appendChild(note);
    try {
      const r = await request("tools/call", {name: "answer_lucius_approval", arguments: {
        conversation_id: state.conversation_id, card_id: cardId,
        card_token: state.tokens[cardId] || "", decision}});
      if (r && r.isError) { note.textContent = (r.content && r.content[0] && r.content[0].text) || "That didn't work."; return; }
      render(r);
    } catch (e) { note.textContent = "That didn't work. You can answer it in Open Net Worth."; }
  }
  window.addEventListener("message", (e) => {
    if (e.source !== window.parent) return;
    const m = e.data; if (!m || m.jsonrpc !== "2.0") return;
    if (m.id !== undefined && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.reject(m.error) : p.resolve(m.result); return;
    }
    if (m.method === "ui/notifications/tool-result") render(m.params);
  });
  // The content's own height: the page's scroll height is never below the frame, so it could
  // only ever grow.
  const size = () => { const r = document.body.getBoundingClientRect();
    send({method: "ui/notifications/size-changed", params: {
      width: Math.ceil(r.width), height: Math.ceil(r.height)}}); };
  new ResizeObserver(size).observe(document.body);
  const ready = () => { send({method: "ui/notifications/initialized", params: {}}); size(); };
  request("ui/initialize", {appCapabilities: {}, appInfo: {name: "Open Net Worth", version: "1"},
    protocolVersion: "2026-01-26"}).then(ready, ready);
})();
</script></body></html>
"""
