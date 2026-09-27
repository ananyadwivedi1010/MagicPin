# MagicPin Merchant AI Assistant

This project implements a lightweight, deterministic merchant AI assistant for the magicpin challenge. It follows the repo brief by exposing the required judge-facing HTTP endpoints and composing high-quality WhatsApp-facing messages from the four context layers: category, merchant, trigger, and optional customer context.

## Approach

The bot is intentionally deterministic and fast. It does not depend on an external LLM at runtime. Instead, it:

- loads category, merchant, customer, and trigger context from the judge’s `/v1/context` calls,
- selects a relevant message strategy based on the trigger kind,
- uses concrete merchant and category details already present in the dataset,
- chooses a clear primary CTA only when the situation requires one,
- avoids inventing data or making unsupported claims.

## Message rules enforced

- merchant-facing messages use `send_as: "vera"`
- customer-facing recall/reminder flows use `send_as: "merchant_on_behalf"`
- triggers with strong action intent use a direct yes/stop or open-ended CTA
- seasonality, research, and opportunity triggers prioritize relevance and timing
- all messages stay concise and grounded in the supplied data

## Submission artifact

The file `submission.jsonl` contains 30 deterministic test rows generated from the canonical dataset using the same `compose()` function used by the live server. This keeps the output consistent with the judge contract and gives a reproducible submission artifact.

## How to run

1. Install dependencies:
   python -m pip install -r requirements.txt
2. Start the server:
   python -m uvicorn app:app --host 0.0.0.0 --port 8080
3. Point the judge to `http://localhost:8080`.

## API key note

The judge simulator itself requires an LLM provider key unless you choose `ollama` mode. If you are not using an external LLM, set the provider to `ollama` and run a local Ollama server, or provide a valid API key for OpenAI/Anthropic/Gemini/etc. The bot server itself does not require any API key.
