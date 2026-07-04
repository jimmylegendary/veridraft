"""Backend providers for the portable PaperOrchestra runner.

PaperOrchestra skills are host-agnostic: the LLM + tools come from a host agent.
This module abstracts the host so one config can target any backend:

  - "claude-code" : the `claude` CLI in headless print mode (Claude models).
  - "openclaw"    : `openclaw agent` (whatever provider/model openclaw is configured for).
  - "api"         : a self-hosted OpenAI-compatible endpoint (base_url/api_key/model),
                    driven through an OSS agent CLI (opencode or aider) that gives the
                    model the tools a skill step needs (file/bash/web).

Each provider runs ONE agentic session for a pipeline step and returns its text.
Every call is wrapped in a hard timeout — an agent step never hangs the runner.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess


class ProviderError(RuntimeError):
    pass


def dispatch(cfg: dict, prompt: str, cwd: str, timeout: int = 1800) -> str:
    """Run one agentic session for a step against the configured backend. Returns text."""
    backend = cfg.get("backend", "claude-code")
    fn = {
        "claude-code": _claude_code,
        "openclaw": _openclaw,
        "api": _api,
        "codex": _codex,
    }.get(backend)
    if fn is None:
        raise ProviderError(f"unknown backend {backend!r} (claude-code|openclaw|api|codex)")
    return fn(cfg, prompt, cwd, timeout)


def _run(argv: list[str], cwd: str, timeout: int, env: dict | None = None) -> str:
    if not shutil.which(argv[0]):
        raise ProviderError(f"backend CLI not found on PATH: {argv[0]}")
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env={**os.environ, **(env or {})})
    except subprocess.TimeoutExpired:
        raise ProviderError(f"backend {argv[0]} timed out after {timeout}s (step too long / stuck)")
    if p.returncode != 0:
        raise ProviderError(f"backend {argv[0]} exit {p.returncode}: {p.stderr.strip()[:400]}")
    return p.stdout


def _claude_code(cfg: dict, prompt: str, cwd: str, timeout: int) -> str:
    # headless print mode; model + tools come from the claude CLI config
    argv = ["claude", "-p", prompt, "--output-format", "text",
            "--permission-mode", cfg.get("permission_mode", "acceptEdits")]
    if cfg.get("model"):
        argv += ["--model", cfg["model"]]
    # In hosts that inherit user MCP servers, a nested `claude -p` can hang on the
    # first tool call while MCP servers initialize. Optionally disable inherited MCP
    # and pre-approve the built-in tools the pipeline needs (no blanket bypass).
    tools = cfg.get("claude_allowed_tools")
    if tools:
        if isinstance(tools, str):          # a string would spread per-character — normalize
            tools = [t for t in re.split(r"[,\s]+", tools) if t]
        argv += ["--allowedTools", *tools]
    if cfg.get("claude_disable_mcp", True):   # default ON for claude-code: a nested `claude -p`
        argv += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']   # hangs on MCP init
    return _run(argv, cwd, timeout)


def _openclaw(cfg: dict, prompt: str, cwd: str, timeout: int) -> str:
    agent = cfg.get("agent", "main")
    argv = ["openclaw", "agent", "--agent", agent, "--message", prompt, "--json"]
    if cfg.get("model"):
        argv += ["--model", cfg["model"]]
    out = _run(argv, cwd, timeout)
    # openclaw may print a version/warning preamble before the JSON envelope; decode
    # from the first '{'. Envelope: {runId, status, result:{finalAssistantVisibleText,...}}
    data = _first_json(out)
    if isinstance(data, dict):
        res = data.get("result") if isinstance(data.get("result"), dict) else {}
        meta = res.get("meta") if isinstance(res.get("meta"), dict) else {}
        txt = meta.get("finalAssistantVisibleText") or meta.get("finalAssistantRawText")
        if not txt:
            payloads = res.get("payloads") if isinstance(res.get("payloads"), list) else []
            if payloads and isinstance(payloads[0], dict):
                txt = payloads[0].get("text")
        return txt or out
    return out


def _first_json(s: str):
    i = s.find("{")
    while i != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(s[i:])
            return obj
        except ValueError:
            i = s.find("{", i + 1)
    return None


def _codex(cfg: dict, prompt: str, cwd: str, timeout: int) -> str:
    # `codex exec` REFUSES to write files without a sandbox/permission grant — and pipeline steps
    # MUST write files. Writing requires a DELIBERATE consent decision (it disables approvals +
    # sandbox for the autonomous run), so it is EXPLICIT opt-in config, never a silent default:
    #   codex_full_access: true   → --dangerously-bypass-approvals-and-sandbox (the only mode that
    #                               actually writes; use only in a disposable/CI sandbox), or
    #   codex_sandbox: "workspace-write"  → -s <policy> (may still not write, per the CLI).
    argv = ["codex", "exec"]
    if cfg.get("codex_full_access"):
        argv += ["--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check"]
    elif cfg.get("codex_sandbox"):
        argv += ["--skip-git-repo-check", "-s", str(cfg["codex_sandbox"])]
    if cfg.get("model"):
        argv += ["--model", cfg["model"]]
    argv.append(prompt)
    return _run(argv, cwd, timeout)


def _api(cfg: dict, prompt: str, cwd: str, timeout: int) -> str:
    """Self-hosted OpenAI-compatible endpoint, driven through an OSS agent CLI.

    A raw LLM endpoint has no tools, so we route through `opencode` (preferred) or
    `aider`, pointing them at base_url/api_key/model via env. This gives the model
    the file/bash/web tools a skill step needs.
    """
    base_url = _resolve(cfg.get("base_url"))
    api_key = _resolve(cfg.get("api_key"))
    model = cfg.get("model")
    if not (base_url and model):
        raise ProviderError("api backend requires base_url + model (and usually api_key)")
    env = {
        "OPENAI_BASE_URL": base_url, "OPENAI_API_BASE": base_url,
        "OPENAI_API_KEY": api_key or "sk-noauth",
    }
    driver = cfg.get("api_driver") or ("opencode" if shutil.which("opencode") else "aider")
    if driver == "opencode":
        argv = ["opencode", "run", "--model", model, prompt]
    else:  # aider
        argv = ["aider", "--model", model, "--no-auto-commit", "--yes",
                "--message", prompt]
    return _run(argv, cwd, timeout, env=env)


def _resolve(v):
    """Allow `env:VARNAME` indirection so secrets aren't stored in the config file."""
    if isinstance(v, str) and v.startswith("env:"):
        return os.environ.get(v[4:], "")
    return v


def probe(cfg: dict) -> tuple[bool, str]:
    """Cheap reachability check for the configured backend (no full step)."""
    backend = cfg.get("backend", "claude-code")
    if backend not in ("claude-code", "openclaw", "codex", "api"):
        return False, f"unknown backend {backend!r} (claude-code|openclaw|api|codex)"
    cli = {"claude-code": "claude", "openclaw": "openclaw", "codex": "codex"}.get(backend)
    if backend == "api":
        driver = cfg.get("api_driver") or ("opencode" if shutil.which("opencode") else "aider")
        cli = driver
    if not cli or not shutil.which(cli):
        return False, f"backend CLI {cli!r} not found on PATH"
    return True, f"backend {backend} CLI present ({cli})"
