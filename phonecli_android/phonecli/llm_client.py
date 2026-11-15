"""OpenAI-compatible API client for text LLM and vision (VLM) calls."""

# The app runs this file on Python 3.8 (Chaquopy), where `list[str]` in an
# annotation is evaluated at def time and fails; the future import makes
# annotations strings on every version.
from __future__ import annotations

import base64
import sys
from typing import Optional

from phonecli.token_usage import token_usage


def _get_client(api_key: str, api_base: str):
    """Lazy import OpenAI client."""
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=api_base, timeout=60.0)


def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _record_usage(response, label: str = ""):
    """Extract usage from OpenAI/OpenRouter response and accumulate."""
    try:
        usage = response.usage
        token_usage.add(
            prompt_tokens=getattr(usage, 'prompt_tokens', 0) or 0,
            completion_tokens=getattr(usage, 'completion_tokens', 0) or 0,
            cache_read_tokens=getattr(usage, 'cache_read_input_tokens', 0) or 0,
            cache_write_tokens=getattr(usage, 'cache_creation_input_tokens', 0) or 0,
            label=label,
        )
    except Exception:
        pass


def text_completion(
    system_prompt: str,
    user_prompt: str,
    api_key: str = "EMPTY",
    api_base: str = "http://localhost:8002/v1",
    model: str = "Qwen/Qwen2.5-3B-Instruct",
    max_tokens: int = 4096,
    temperature: float = 0.0,
    label: str = "text",
) -> str:
    """Send a text-only completion request."""
    client = _get_client(api_key, api_base)
    extra = {}
    if "deepseek" in api_base.lower() or "deepseek" in model.lower():
        extra["extra_body"] = {"thinking": {"type": "disabled"}}

    try:
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            **extra,
        )
        content = r.choices[0].message.content or ""
        if not content:
            msg = r.choices[0].message
            if hasattr(msg, "reasoning_content") and msg.reasoning_content:
                content = msg.reasoning_content
            elif hasattr(msg, "reasoning") and msg.reasoning:
                content = msg.reasoning
            elif hasattr(msg, "model_extra") and msg.model_extra:
                content = msg.model_extra.get("reasoning", "")
        _record_usage(r, label=label)
        return content
    except Exception as e:
        print(f"LLM error: {e}", file=sys.stderr)
        raise


def vision_completion(
    system_prompt: str,
    user_text: str,
    image_paths: list[str],
    api_key: str = "EMPTY",
    api_base: str = "http://localhost:8002/v1",
    model: str = "Qwen/Qwen2.5-3B-Instruct",
    max_tokens: int = 4096,
    temperature: float = 0.0,
    label: str = "vision",
) -> str:
    """Send a vision (image + text) completion request."""
    client = _get_client(api_key, api_base)

    content = [{"type": "text", "text": user_text}]
    for path in image_paths:
        b64 = _encode_image(path)
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    try:
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        _record_usage(r, label=label)
        return r.choices[0].message.content or ""
    except Exception as e:
        print(f"VLM error: {e}", file=sys.stderr)
        raise
