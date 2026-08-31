"""Manual tool-use loop over the 4 fixed tools in src/tools.py. The model's
only job is intent classification and parameter extraction -- every number
it returns was computed by src/tools.py, never by the model itself
(part2-handoff.md section 5.1).

Stateless by design: one question in, one resolved answer out, no
cross-question memory. ponytail: none of the brief's sample questions are
follow-ups; add a message-history param if multi-turn chat is ever needed.
"""

import json
import os

import anthropic
import duckdb
from dotenv import load_dotenv

from tools import GENERATION_POTENTIAL_PROXY, TOOL_DISPATCH, TOOL_SCHEMAS

MODEL = "claude-sonnet-5"
MAX_ITERATIONS = 5  # defensive cap, not a real multi-round need with 4 fixed tools

SYSTEM_PROMPT = (
    "You answer questions about solar radiation and wind speed data for 3 sites using the "
    "4 tools provided. You are constrained to intent classification and parameter "
    "extraction; all computation happens in the tools, never in your own reasoning. Every "
    "figure the user sees must come from a tool result -- never compute, estimate, or invent "
    "a number yourself.\n\n"
    "If none of the 4 tools can answer the question (e.g. a forecast, or anything outside "
    "this dataset's scope), say so plainly instead of guessing or calling a tool that doesn't "
    "really fit.\n\n"
    f"When asked about generation potential: {GENERATION_POTENTIAL_PROXY}"
)


def has_api_key() -> bool:
    load_dotenv()
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def ask(question: str, con: duckdb.DuckDBPyConnection) -> dict:
    """Run one question through the tool-use loop. Returns a dict with the
    final answer plus the resolved tool call for auditability, or an
    error message if the API call itself failed."""
    load_dotenv()
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": question}]
    # Track the *first* tool call made for the resolved-call display -- if the model fans out
    # to multiple tools in one turn, all are executed and fed back, but the UI shows the first.
    tool_name, tool_input, tool_result, raw_model_text = None, None, None, None

    try:
        for _ in range(MAX_ITERATIONS):
            response = client.messages.create(
                model=MODEL, max_tokens=1024, system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS, messages=messages,
            )
            raw_model_text = raw_model_text or "\n".join(
                b.text for b in response.content if b.type == "text"
            )

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            messages.append({"role": "assistant", "content": response.content})

            if not tool_use_blocks:
                answer = next((b.text for b in response.content if b.type == "text"), "")
                return {
                    "answer": answer, "tool_name": tool_name, "tool_input": tool_input,
                    "tool_result": tool_result, "raw_model_text": raw_model_text, "error": None,
                }

            # Parallel tool use is on by default -- one turn can carry several tool_use
            # blocks, and every one of them needs a matching tool_result in the next message.
            tool_results = []
            for block in tool_use_blocks:
                result = TOOL_DISPATCH[block.name](con, **block.input)
                if tool_name is None:
                    tool_name, tool_input, tool_result = block.name, block.input, result
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})
            messages.append({"role": "user", "content": tool_results})

        return {
            "answer": "I wasn't able to resolve an answer in time.", "tool_name": tool_name,
            "tool_input": tool_input, "tool_result": tool_result, "raw_model_text": raw_model_text,
            "error": None,
        }
    except anthropic.AuthenticationError:
        return _error_result("The API key is invalid or missing.")
    except anthropic.RateLimitError:
        return _error_result("Rate limited by the Anthropic API -- try again shortly.")
    except anthropic.APIConnectionError:
        return _error_result("Couldn't reach the Anthropic API -- check the network connection.")
    except anthropic.APIStatusError as e:
        return _error_result(f"Anthropic API error ({e.status_code}): {e.message}")


def _error_result(message: str) -> dict:
    return {"answer": None, "tool_name": None, "tool_input": None, "tool_result": None, "raw_model_text": None, "error": message}
