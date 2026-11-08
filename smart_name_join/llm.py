"""Tiny LLM client: standard library only, so the plugin installs with zero extra packages.

Providers
  ollama     local, free:      url (default http://localhost:11434), model (e.g. qwen3:4b)
  openai     any OpenAI-compatible /chat/completions endpoint (OpenAI, OpenRouter, LM Studio, vLLM ...)
  anthropic  Claude Messages API (needs an API key)
  claude_cli the `claude` command (Claude Code) on PATH, uses your Claude subscription, no API key

Note on the Anthropic call: it is raw HTTP on purpose. QGIS ships its own Python and asking every user to
pip-install the `anthropic` SDK into it is the #1 reason AI plugins fail to install.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODELS = {"ollama": "qwen3:4b", "openai": "gpt-4o-mini", "anthropic": "claude-opus-5-5", "claude_cli": ""}


class LLMError(RuntimeError):
    pass


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"content-type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"HTTP {e.code} from {url}: {detail}") from None
    except urllib.error.URLError as e:
        raise LLMError(f"Cannot reach {url}: {e.reason}") from None


def call(provider, system, user, model="", url="", api_key="", timeout=300, schema=None):
    """Return the model's text answer (expected to be JSON)."""
    model = model or DEFAULT_MODELS.get(provider, "")
    if provider == "ollama":
        base = (url or "http://localhost:11434").rstrip("/")
        body = {"model": model, "stream": False, "format": schema or "json", "think": False,
                "options": {"temperature": 0},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        return _post(base + "/api/chat", body, {}, timeout)["message"]["content"]

    if provider == "openai":
        base = (url or "https://api.openai.com/v1").rstrip("/")
        key = api_key or os.environ.get("OPENAI_API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")
        body = {"model": model, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        d = _post(base + "/chat/completions", body, {"authorization": f"Bearer {key}"} if key else {}, timeout)
        return d["choices"][0]["message"]["content"]

    if provider == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise LLMError("No Anthropic API key: set one in the algorithm (authentication config) or ANTHROPIC_API_KEY")
        body = {"model": model, "max_tokens": 16000, "system": system,
                "output_config": {"effort": "low"},
                "fallbacks": "default",
                "messages": [{"role": "user", "content": user}]}
        if schema:
            body["output_config"]["format"] = {"type": "json_schema", "schema": schema}
        d = _post(ANTHROPIC_URL, body, {"x-api-key": key, "anthropic-version": "2026-10-21",
                                         "anthropic-beta": "server-side-fallback-2026-07-01"}, timeout)
        if d.get("stop_reason") == "refusal":
            raise LLMError(f"Claude declined the request: {d.get('stop_details')}")
        if d.get("stop_reason") == "max_tokens":
            raise LLMError("Claude hit max_tokens; lower the batch size")
        return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")

    if provider == "claude_cli":
        exe = url if url and os.path.exists(url) else find_claude()
        if not exe:
            raise LLMError("`claude` command not found on PATH (install Claude Code, or pick another provider)")
        cmd = [exe, "-p", "--output-format", "json", "--max-turns", "1", "--tools", ""]
        if model:
            cmd += ["--model", model]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        p = subprocess.run(cmd, input=system + "\n\n" + user, capture_output=True, text=True,
                           encoding="utf-8", timeout=timeout, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.returncode != 0:
            raise LLMError(f"claude CLI failed: {(p.stderr or p.stdout)[:500]}")
        return json.loads(p.stdout).get("result", "")

    raise LLMError(f"Unknown provider {provider!r}")


def find_claude():
    """QGIS often starts with its own PATH, so also look where the installers put `claude`."""
    exe = shutil.which("claude")
    if exe:
        return exe
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
    spots = [os.path.join(home, ".local", "bin", "claude.exe"), os.path.join(home, ".local", "bin", "claude"),
             os.path.join(local, "Microsoft", "WinGet", "Links", "claude.exe"),
             os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
    pk = os.path.join(local, "Microsoft", "WinGet", "Packages")
    if os.path.isdir(pk):
        spots += [os.path.join(pk, d, "claude.exe") for d in os.listdir(pk) if d.startswith("Anthropic.ClaudeCode")]
    return next((p for p in spots if os.path.isfile(p)), None)


def parse_json(text):
    """Models sometimes wrap JSON in prose or ```json fences; take the outermost object."""
    text = re.sub(r"```(?:json)?", "", text or "")
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise LLMError(f"No JSON in model answer: {text[:200]!r}")
    return json.loads(text[a:b + 1])


class CachedLLM:
    """Same prompt -> same answer from disk. Saves money and makes re-runs reproducible."""

    def __init__(self, provider, model="", url="", api_key="", cache_path=None, timeout=300, log=print):
        self.provider, self.model, self.url, self.api_key = provider, model or DEFAULT_MODELS.get(provider, ""), url, api_key
        self.cache_path, self.timeout, self.log = cache_path, timeout, log
        self.cache = {}
        self.calls = 0
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    self.cache = json.load(f)
            except (OSError, ValueError):
                self.cache = {}

    def ask(self, system, user, schema=None):
        h = hashlib.sha256(f"{self.provider}|{self.model}|{system}|{user}".encode("utf-8")).hexdigest()
        if h in self.cache:
            return self.cache[h]["answer"]
        self.calls += 1
        last = None
        for attempt in range(2):
            try:
                text = call(self.provider, system, user, self.model, self.url, self.api_key, self.timeout, schema)
                ans = parse_json(text)
                break
            except (LLMError, ValueError, KeyError) as e:
                last = e
                self.log(f"  AI call failed (attempt {attempt + 1}): {e}")
        else:
            raise LLMError(str(last))
        self.cache[h] = {"answer": ans, "provider": self.provider, "model": self.model, "prompt": user}
        if self.cache_path:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False)
        return ans
