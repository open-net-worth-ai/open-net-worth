"""Strip tool-call syntax that leaked into an assistant's visible text.

Not provider-specific, which is the point. A model emits a real tool call AND narrates the
same call in a markup nobody asked for whenever it is unsure which calling convention it is
in — seen live from GLM-5.2 over OpenRouter (native function calling, no prompt protocol
involved at all), and the CLI transports have their own version of it, which is why
``ToolCallScanner`` already withholds ``<tool_call>`` fences and echoed ``<tool_result>``
blocks on that path.

What lands in the chat looks like this, above a perfectly good tool call that already ran::

    </arg_key><arg_value>[{"automation_id": "…", "instructions": "…"}]</arg_value></tool_call>

The whole segment is dropped rather than de-tagged: what sits BETWEEN the tags is the
serialized arguments, and showing those raw is no better than showing the tags.
"""

from __future__ import annotations

import json
import re

from backend.core.chat_context_markers import (  # noqa: F401 — re-exported: the markers' home is core
    HARNESS_NOTE_CLOSE,
    HARNESS_NOTE_OPEN,
    LEGACY_HARNESS_OPENINGS,
    ONBOARDING_INIT,
)

# Every shape a model reaches for: our own <tool_call> fence, and the XML-ish form
# (<function=name><parameter=x><arg_key>…</arg_key><arg_value>…</arg_value>) common to
# several open-weight models. None of them can occur in prose, which is what makes their
# presence sufficient evidence that the segment is machine syntax rather than an answer.
# ``tool_call`` alone was not enough: the CLI transport writes the transcript the model reads,
# and _block_to_text emits `<tool_call_id>…</tool_call_id>` after every tool_use. The name has a
# `_id` suffix, so `tool_call(?:=…)?>` never matched it and it rendered verbatim. A trailing
# `_word` is now allowed on every tag, and tool_result/function_calls/invoke are named too —
# every shape that appears in the transcript the model is imitating.
_RESIDUE_TAG_RE = re.compile(
    r"</?(?:tool_call|tool_result|tool_use|function|function_calls|parameter|arg_key|arg_value"
    r"|invoke|call)(?:_[a-z]+)?(?:[ =][^<>]*)?>",
    re.IGNORECASE,
)


# A message the HARNESS wrote, not the owner. It rides `role: "user"` because that is the
# only role every transport has — Anthropic grew a real `system` message role (Opus 5 / 4.8 /
# Fable 5 / Mythos 5, not Sonnet 5), but the CLI transports flatten to a text transcript and
# have no such channel, so a portable marker is the common denominator.
#
# The wire role is not the problem; the ATTRIBUTION is. Unmarked, these are indistinguishable
# from something the owner typed, and four live bugs came out of that: the completion nudge
# rendered as the owner's own message, a continuation re-declared its plan, attachment refs
# showed as message text, and a proactive alert marker became a month-old conversation's
# TITLE and dragged it to the top of the chat list (owner, 2026-08-21).
#
# Guillemets match `core/untrusted.py`'s fence style and cannot occur in the machine syntax
# this module already strips. Unlike that fence this one carries NO nonce: it makes no
# security claim, so there is nothing to forge — a reader that trusts it is only deciding
# whether to draw a bubble, never whether to obey an instruction.
def harness_note(text: str) -> str:
    """Wrap harness-authored text so every reader can tell it apart from the owner's."""
    return f"{HARNESS_NOTE_OPEN}{text}{HARNESS_NOTE_CLOSE}"


# Harness-authored bodies that PREDATE the wrapper above (added 2026-08-21) and are
# therefore stored BARE in every transcript written before then. Recognised by their
# literal opening because that is the only marker those rows have: without this, a
# pre-wrapper proactive-alert trigger keeps being drawn as the owner's own message and
# can still name the conversation (the owner's own Telegram session was titled
# "[Automated monitor — the following message was delivered to" this way). Matched on a
# distinctive full opening clause, not a loose keyword, so ordinary prose about
# monitoring cannot trip it.
_LEGACY_HARNESS_OPENINGS = LEGACY_HARNESS_OPENINGS


# The onboarding kickoff sentinel. `ChatPanel` sends it to make Lucius speak first on a brand-new
# instance; the user never typed it and must never see it. Kept BARE rather than wrapped in the
# fence above because `ai/prompts.py` and `ai/assistant.py` both match it by EXACT equality —
# wrapping it would silently break the greeting it exists to trigger. Matched exactly here for the
# same reason, and so a person who merely BEGINS a message with the token is not silenced.


def is_harness_note(text: str) -> bool:
    """True when a message body is harness-authored — never rendered as the owner's words,
    never used to name a conversation, never counted as the owner touching it."""
    body = text or ""
    if HARNESS_NOTE_OPEN in body:
        return True
    if body.strip() == ONBOARDING_INIT:
        return True
    return body.lstrip().startswith(_LEGACY_HARNESS_OPENINGS)


# The corrective line ``cli_tool_protocol`` emits in place of a call whose JSON would not
# parse. It lives HERE rather than there so the completion gate can recognise it by identity
# instead of by a copy that drifts: its whole purpose is to be read back by the model next
# round, which makes "did this round end on it?" a question the gate has to be able to ask.
UNPARSED_CALL_NOTICE = (
    "[tool call not executed — its JSON was invalid and could not be parsed; "
    "retry with a single well-formed JSON object]"
)

# An ATTEMPTED call, as opposed to a mention of the syntax. The distinction earns its keep:
# a false positive here spends a whole extra model round (~400k tokens on an Automation), so
# an opening call tag alone is not enough — it must be followed by the body of a real call,
# either a JSON object or one of the XML dialect's parameter children. That is what separates
# the live failures (`<call>{"name": "update_run_task", …`) from a sentence discussing
# `<invoke name="telegram_send_message">`. A trailing `_word` is excluded by \b, so the transcript's own
# `<tool_call_id>` echo — which is not an attempt at anything — never matches.
#
# GLM's native dialect puts the function NAME bare between the opening tag and the first
# child — `<tool_call>read_doc\n<arg_key>doc_id</arg_key>…` — so one optional identifier is
# allowed there. Still not a mention: the identifier must itself be followed by the body.
# Without it, three follow-up rounds on run_47f8605fdd98 (glm-5.3-flash, 2026-09-22) each
# spent 15–17 tokens — one real tool call's worth — on markup `strip_tool_syntax` erased
# and this detector did not see, and every one ended the turn `ok` on nothing.
#
# A ZERO-argument call has no body to demand — `<invoke name="list_accounts"></invoke>` —
# so a NAMED invoke closed straight away is its own attempt. The name is what separates it
# from a sentence about `<invoke></invoke>` tags.
_CALL_ATTEMPT_RE = re.compile(
    r"<\s*(?:antml:)?(?:call|tool_call|function_calls|invoke)\b[^<>]*>"
    r"\s*(?:[A-Za-z_][\w.-]*\s*)?(?:\{|<\s*(?:antml:)?(?:parameter|arg_key|invoke)\b)"
    r"|<\s*(?:antml:)?invoke\b[^<>]*\bname\s*=[^<>]*>\s*<\s*/\s*(?:antml:)?invoke\s*>",
    re.IGNORECASE,
)


def looks_like_unexecuted_tool_call(text: str) -> bool:
    """True when the model WROTE a tool call instead of MAKING one.

    Only meaningful about a round that emitted no real ``tool_use`` block: alongside a
    genuine call the same markup is mere residue for ``strip_tool_syntax`` to cut. With no
    call, it is the whole failure — nothing ran, and every sentence around it describing
    what the call returned is invented.
    """
    if not text:
        return False
    return bool(_CALL_ATTEMPT_RE.search(text)) or UNPARSED_CALL_NOTICE in text


# The same transcript is Human:/Assistant:/System: delimited, so the model also imitates the
# turn marker — a line that is nothing but `H:`, `A:` or `S:`. Only ever matched on its own
# line, so a real sentence starting "H: ..." is untouched. `System:` joined the set when the
# CLI transcript stopped rendering harness-authored messages as the model's own words.
_ROLE_MARKER_RE = re.compile(r"^[ \t]*(?:H|A|S|Human|Assistant|System):[ \t]*$", re.MULTILINE)

# The model talking to itself about the calling protocol, after a call it wrote in the wrong
# dialect: "Wait, I must use the required format." / "Actually, I need to use the correct
# format." / "I need to use the proper tool call format." Every persona in the 2026-10-13 QA
# saw one mid-onboarding, and read it as the app breaking. Whole lines only, and only the
# protocol phrasing (a required/correct/proper FORMAT or tool-call STRUCTURE), so prose about
# a file's format or a date format is untouched. A lone "ait" line is the same sentence with
# its "W" lost to a stripped tag, and `_call>` the tail of one.
_FORMAT_NARRATION_RE = re.compile(
    r"^[ \t]*(?:(?:w?ait|actually|hmm|oops|okay|ok|right)\b[ \t,.!:\u2014\u2013-]*)?"
    r"(?:let me[ \t]+|i[ \t]+(?:must|need to|should|will|have to)[ \t]+)?"
    r"(?:fix the format and[ \t]+)?(?:re-?)?(?:use|using|try|follow|switch to)[ \t]+(?:the[ \t]+)?"
    r"(?:required|correct|proper|right|expected)[ \t]+(?:tool[ \t_-]?call[ \t]+)?"
    r"(?:format|structure|syntax)[ \t]*(?:instead|here|now|this time)?[ \t.:!]*$"
    # Any short line naming the calling protocol itself ("Correction: I need to emit the call
    # in the proper format.", "tool calls must use the tool_call block") — vocabulary a
    # finance answer never uses.
    r"|^[^\n]{0,120}?(?:\btool[ \t_-]?calls?\b|\bcall[ \t]+(?:format|block|structure|syntax)\b"
    r"|\bemit the call\b)[^\n]{0,120}$"
    r"|^[ \t]*w?ait[ \t.!]*$"
    r"|(?<![\w<])_calls?>",
    re.IGNORECASE | re.MULTILINE,
)

# A model that forgets even a fence tag writes the call's raw JSON body straight into its
# reply — no ``<...>`` markup at all for ``_RESIDUE_TAG_RE`` to catch. Live 2026-08-18 (an
# Automation run, claude_cli): the reply was
#
#     {"name": "update_run_task", "input": {"task_id": "1", "status": "in_progress"}}
#
# immediately followed by a SECOND, properly-fenced call in the same round — the model tried
# to make two calls in one reply and only the second one got a fence. ``cli_tool_protocol.looks_like_an_unfenced_tool_
# call`` already validates this exact shape, but only when the ENTIRE reply IS the blob; here
# it is a fragment inside a larger reply, so that check never fires — and nothing in this
# module's tag-based scan sees it either, since there is no tag to match.
#
# Detected the same narrow way: a bare ``{...}`` (or single-item ``[{...}]``) whose keys are a
# SUBSET of the documented call shape, located with ``json.JSONDecoder.raw_decode`` rather
# than a brace-counting regex — a real call's own "input" nests further braces/arrays a regex
# cannot balance, but the decoder walks them correctly. Duplicated here rather than imported
# from ``cli_tool_protocol`` because this module is deliberately NOT provider-specific (see
# the module docstring) — the shape check itself is generic, only the transport that leaves
# it unfenced is not.
_JSON_CALL_KEYS = {"name", "tool_name", "input"}
_json_decoder = json.JSONDecoder()


def _embedded_call_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"[{\[]", text):
        try:
            obj, end = _json_decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        items = obj if isinstance(obj, list) else [obj]
        if len(items) != 1 or not isinstance(items[0], dict):
            continue
        item = items[0]
        if not item or not set(item.keys()) <= _JSON_CALL_KEYS:
            continue
        name = item.get("name") or item.get("tool_name")
        if name and isinstance(name, str):
            spans.append((m.start(), end))
    return spans


_FENCE_BEFORE_RE = re.compile(r"```[\w-]*[ \t]*\n?[ \t]*$")
_FENCE_AFTER_RE = re.compile(r"^[ \t]*\n?[ \t]*```[ \t]*(?:\n|$)")


# A reply that OPENS on a bare fence line it never closes. Live 2026-10-01
# (automation-run-run_ca58530da2cf, claude_cli): the closing checklist was stored as
# "\n```\n\nClosing checklist:…" with no other fence — the model started to fence a call,
# wrote prose instead, and the whole reply rendered as one monospace code block.
_LEADING_BARE_FENCE_RE = re.compile(r"\A\s*```[ \t]*(?:\n|\Z)")
# ...or ENDS on one with nothing after it: a call written bare and then "closed" with a fence
# it never opened (`<tool_call>…</tool_call>\n```` — persona QA round 9, 2026-10-14). Alone it
# is an empty code box; glued to the next round's text it swallowed the whole first paragraph.
_TRAILING_FENCE_RE = re.compile(r"\n[ \t]*```[\w+-]*\s*\Z")


def _drop_unclosed_edge_fence(text: str) -> str:
    if not text.count("```") % 2:
        return text
    if _LEADING_BARE_FENCE_RE.match(text):
        return _LEADING_BARE_FENCE_RE.sub("", text, count=1).lstrip("\n")
    trimmed = _TRAILING_FENCE_RE.sub("", text, count=1)
    return trimmed.rstrip() if trimmed != text else text


def strip_tool_syntax(text: str) -> str:
    """Drop the leaked machine-syntax REGION, keeping the prose around it.

    Text with no protocol tag or embedded call blob is returned UNCHANGED — this must never
    touch ordinary prose — except an unclosed fence the reply opens or ends on, which is never
    prose (see _LEADING_BARE_FENCE_RE).

    Only the span from the first residue to the last is removed, NOT the whole string. Erasing
    the whole string looked equivalent while the residue could only come from the model's own
    confusion, but it can also arrive QUOTED from untrusted input: an email subject reading
    ``Invoice 4471 <parameter>`` is enough for an Automation that summarizes it verbatim to have
    its entire narration replaced by ``""`` — in the transcript as well as on screen, since this
    runs before the turn is persisted. That hands an attacker a way to make a run's account of
    what it did disappear. Bounding the cut means the worst case is a mangled sentence, never a
    silent blank.
    """
    if not text:
        return text
    text = _drop_unclosed_edge_fence(text)
    spans = [(m.start(), m.end()) for m in _RESIDUE_TAG_RE.finditer(text)]
    spans.extend(_embedded_call_spans(text))
    if spans:
        start = min(s for s, _ in spans)
        end = max(e for _, e in spans)
        # A markdown fence hugging the residue belongs to it — the XML dialect is routinely
        # written inside ``` (or closed with a lone one it never opened), and left behind that
        # fence opens a code block that swallows the rest of the reply.
        head = text[:start]
        if head.count("```") % 2:  # an OPENER; an even count means it closes real code
            head = _FENCE_BEFORE_RE.sub("", head)
        tail = _FENCE_AFTER_RE.sub("", text[end:])
        text = head + " " + tail
    elif not _ROLE_MARKER_RE.search(text) and not _FORMAT_NARRATION_RE.search(text):
        return text
    text = _ROLE_MARKER_RE.sub("", text)
    text = _FORMAT_NARRATION_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def drop_call_preamble(text: str, *, has_calls: bool) -> str:
    """The text of a round that ends in tool calls, minus a lone lead-in to those calls.

    A single short line ending in a colon ("Actually, emitting properly:", "Now let me pull
    that up:") is the model announcing its next call, not something said to the user; the
    phrasings are endless (QA 2026-10-13 found a new one each round), the shape is not.
    A round with no calls is an answer and is never touched; neither is anything longer.
    """
    line = (text or "").strip()
    if has_calls and line.endswith(":") and "\n" not in line and len(line) <= 80:
        return ""
    return text


# A line that is nothing but a markdown fence: bare, or carrying a language tag.
_FENCE_LINE_RE = re.compile(r"^[ \t]*```([\w+-]*)[ \t]*$")


class LiveLineFilter:
    """A round's text as it streams, minus what ``strip_tool_syntax`` cuts at save time — so the
    reader never sees it live either. Persona QA 2026-10-14 met "Correction to my format:
    emitting the proper tool call blocks." on screen though the saved reply was clean: the chat
    keeps what it streamed. Only a SHORT line is held, until it ends or grows past the longest
    self-talk seen, so an ordinary answer streams as before.

    A bare opening fence is held too, with everything after it, until the block closes. Whether
    it ever does is unknowable mid-stream, and an opener the round ends on (a call "closed" with
    a fence it never opened) is an empty code box that, released after the tool row, glued onto
    the next round's text and turned the whole first paragraph into the fence's info string —
    the same fence ``strip_tool_syntax`` drops from the saved reply. Real code is released whole
    when its closing fence arrives, so it streams in one piece rather than line by line."""

    _HOLD = 120

    def __init__(self) -> None:
        self._held = ""
        self._streaming = False  # the current line outgrew _HOLD and passes straight through
        self._code = False  # inside a fenced block
        self._gate: str | None = None  # a bare opening fence and what follows, until it closes
        self._gate_leads = False  # that fence is the first thing the round shows
        self._shown = False  # anything but whitespace has left the filter this round

    def feed(self, text: str) -> str:
        out: list[str] = []
        for piece in re.split(r"(\n)", text):
            if piece == "\n":
                out.append(self._end_line())
            elif piece and self._streaming:
                out.append(self._emit(piece))
            elif piece:
                self._held += piece
                if len(self._held) > self._HOLD:
                    out.append(self._emit(self._held))
                    self._held, self._streaming = "", True
        return "".join(out)

    def flush(self, *, has_calls: bool = False) -> str:
        """Release the line still held — at a tool call (``has_calls``: a lone lead-in to it is
        dropped, see ``drop_call_preamble``) or at the end of the round."""
        return self._flush_line(has_calls) + self._release_gate()

    def _flush_line(self, has_calls: bool) -> str:
        if self._streaming:
            self._streaming = False
            return ""
        line, self._held = self._held, ""
        if has_calls and not drop_call_preamble(line, has_calls=True):
            return ""
        cleaned = self._clean(line)
        return self._line(cleaned, "") if cleaned else ""

    def _end_line(self) -> str:
        if self._streaming:
            self._streaming = False
            return self._emit("\n")
        line, self._held = self._held, ""
        cleaned = self._clean(line)
        return "" if cleaned is None else self._line(cleaned, "\n")

    def _emit(self, text: str) -> str:
        if self._gate is not None:
            self._gate += text
            return ""
        if text.strip():
            self._shown = True
        return text

    def _line(self, line: str, end: str) -> str:
        """One finished line through the fence gate."""
        fence = _FENCE_LINE_RE.match(line)
        if fence is None or (self._code and fence.group(1)):
            return self._emit(line + end)
        if not self._code:
            self._code = True
            if fence.group(1):
                return self._emit(line + end)
            self._gate_leads = not self._shown
            self._gate = line + end
            return ""
        self._code = False
        if self._gate is None:
            return self._emit(line + end)
        released, self._gate = self._gate + line + end, None
        return self._emit(released)

    def _release_gate(self) -> str:
        """The round is over (or a call interrupts it) with a bare fence still open: drop it
        when it opened the round or has nothing after it, as ``strip_tool_syntax`` does."""
        if self._gate is None:
            return ""
        held, self._gate = self._gate, None
        _, _, body = held.partition("\n")
        if self._gate_leads or not body.strip():
            self._code = False
            held = body.lstrip("\n")
        return self._emit(held)

    @staticmethod
    def _clean(line: str) -> str | None:
        """The line without self-talk; None when nothing but self-talk was on it."""
        if not line.strip():
            return line
        cleaned = _FORMAT_NARRATION_RE.sub("", line)
        return cleaned if cleaned.strip() else None


class RoundText:
    """Streamed assistant text, held one model round at a time, for a surface that shows it.

    Every consumer that rebuilt a reply from raw ``text_delta`` events (the iOS capture, a chat
    channel, an Automation's summary) showed leaked call markup verbatim — live 2026-09-29, a
    lock-screen notification read ``<invoke name="get_payees"></invoke>``. The transcript never
    had this problem because ``assistant.run`` strips each round before persisting it.

    Stripped PER ROUND, never over the joined text: ``strip_tool_syntax`` cuts from the first
    residue to the last, so across rounds one early leak and one late leak would erase every
    honest round between them. Callers mark a round's end with ``boundary()`` on
    ``tool_use_start`` / ``tool_executing`` — the latter is emitted for every provider.
    """

    def __init__(self) -> None:
        self._rounds: list[list[str]] = [[]]

    def add(self, text: str) -> None:
        if text:
            self._rounds[-1].append(text)

    def boundary(self) -> None:
        if self._rounds[-1]:
            self._rounds.append([])

    def raw(self) -> str:
        """Unstripped — for a detector that needs the markup as evidence."""
        return "".join("".join(r) for r in self._rounds).strip()

    def clean(self) -> str:
        out: list[str] = []
        for parts in self._rounds:
            text = "".join(parts)
            stripped = strip_tool_syntax(text)
            if stripped == text:
                out.append(text)
            elif stripped:
                out.append(stripped + "\n\n")
        return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()
