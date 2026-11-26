"""AI router: sends each production task to an AI provider, with fallback.

Each step of the video workflow (script, voice, image, video, captions) is a
"task". For each task you register an ordered list of providers. The router
uses the first provider whose API key is set and whose call succeeds; if it
fails, the next one is tried.

API keys are read from environment variables only. Never hard-code a key here.

Usage:
    python scripts/ai_router.py tasks              # list known tasks
    python scripts/ai_router.py plan voice         # show providers that would be tried
    python scripts/ai_router.py route script "..." # run the task through the router

Adding a provider: write a function that takes a prompt string and returns the
result (or raises an exception on failure), then call register() in the
PROVIDERS section below.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Callable

TASKS = ("script", "voice", "image", "video", "captions")


class RouterError(RuntimeError):
    """Raised when no provider can handle a task."""


@dataclass(frozen=True)
class Provider:
    name: str
    env_var: str  # name of the environment variable holding this provider's API key
    call: Callable[[str], str]


_registry: dict[str, list[Provider]] = {task: [] for task in TASKS}


def register(task: str, provider: Provider) -> None:
    """Append a provider to the fallback chain for a task (order = priority)."""
    if task not in _registry:
        raise ValueError(f"Unknown task '{task}'. Known tasks: {', '.join(TASKS)}")
    _registry[task].append(provider)


def _has_key(provider: Provider) -> bool:
    return bool(os.environ.get(provider.env_var, "").strip())


def plan(task: str) -> list[str]:
    """Return the names of providers that are configured (key set) for a task, in order."""
    if task not in _registry:
        raise ValueError(f"Unknown task '{task}'. Known tasks: {', '.join(TASKS)}")
    return [p.name for p in _registry[task] if _has_key(p)]


def route(task: str, prompt: str) -> str:
    """Run a prompt through the fallback chain for a task and return the first success."""
    if task not in _registry:
        raise ValueError(f"Unknown task '{task}'. Known tasks: {', '.join(TASKS)}")
    if not _registry[task]:
        raise RouterError(f"No providers registered for task '{task}' yet.")

    errors: list[str] = []
    for provider in _registry[task]:
        if not _has_key(provider):
            errors.append(f"{provider.name}: {provider.env_var} is not set")
            continue
        try:
            return provider.call(prompt)
        except Exception as exc:  # fall back to the next provider on any failure
            errors.append(f"{provider.name}: {exc}")

    raise RouterError(f"All providers failed for '{task}': " + "; ".join(errors))


# --- PROVIDERS -------------------------------------------------------------
# Register providers here, in priority order. Example (not active):
#
# def _call_my_tool(prompt: str) -> str:
#     ...  # call the vendor API with os.environ["MY_TOOL_API_KEY"]
#     return result_text
#
# register("voice", Provider("my-tool", "MY_TOOL_API_KEY", _call_my_tool))
# ---------------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Route video-production tasks to AI providers.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tasks", help="List known tasks")
    p_plan = sub.add_parser("plan", help="Show configured providers for a task")
    p_plan.add_argument("task", choices=TASKS)
    p_route = sub.add_parser("route", help="Send a prompt through a task's fallback chain")
    p_route.add_argument("task", choices=TASKS)
    p_route.add_argument("prompt")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "tasks":
            print("\n".join(TASKS))
        elif args.cmd == "plan":
            names = plan(args.task)
            print(", ".join(names) if names else "(no configured providers)")
        elif args.cmd == "route":
            print(route(args.task, args.prompt))
    except (RouterError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
