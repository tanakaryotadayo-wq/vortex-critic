#!/usr/bin/env python3
"""
gemini-cli-critic.py - ACP/PCC Gemini CLI critic hook.

This script uses the same evidence collection and prompt construction core as
vortex-critic.py, then routes the final read-only audit prompt to Gemini CLI in
plan mode. The default model is gemini-3.1-pro-preview.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "1.0.0-acp-gemini-cli"
SCRIPT_NAME = "gemini-cli-critic.py"
DEFAULT_MODEL = "gemini-3.1-pro-preview"
DEFAULT_TIMEOUT_SECONDS = 180
CORE_SCRIPT = Path(__file__).with_name("vortex-critic.py")


def load_core() -> Any:
    spec = importlib.util.spec_from_file_location("vortex_critic_core", CORE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load VORTEX critic core: {CORE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load_core()


def load_config() -> dict[str, Any]:
    config = core.load_config()
    config.update({
        "provider": "gemini-cli",
        "model": DEFAULT_MODEL,
        "geminiBinary": os.environ.get("VORTEX_GEMINI_BIN", "gemini"),
        "geminiTimeoutSeconds": DEFAULT_TIMEOUT_SECONDS,
        "geminiUseAcpFlag": os.environ.get("VORTEX_GEMINI_USE_ACP_FLAG", "0") == "1",
        "ft_data_dir": os.path.expanduser("~/.vortex-critic/gemini-cli"),
    })

    config_path = Path(__file__).with_name("gemini-cli-critic.config.json")
    if config_path.exists():
        try:
            overlay = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(overlay, dict):
                config.update(overlay)
        except Exception as exc:
            print(f"Gemini CLI critic config JSON is invalid: {exc}", file=sys.stderr)

    return core.normalize_config_paths(config)


def collect_script_provenance() -> dict[str, Any]:
    script_path = Path(__file__).resolve()
    info: dict[str, Any] = {
        "name": SCRIPT_NAME,
        "version": SCRIPT_VERSION,
        "path": str(script_path),
        "sha256": core.sha256_file(script_path),
        "provider": "gemini-cli",
        "default_model": DEFAULT_MODEL,
        "shared_core": {
            "path": str(CORE_SCRIPT.resolve()),
            "sha256": core.sha256_file(CORE_SCRIPT.resolve()),
            "version": getattr(core, "SCRIPT_VERSION", "unknown"),
        },
        "trust_policy": getattr(core, "SCRIPT_TRUST_POLICY", ""),
    }

    try:
        stat = script_path.stat()
        info["size_bytes"] = stat.st_size
        info["mtime_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime))
    except OSError:
        pass

    repo_root = core.git_text(script_path.parent, ["rev-parse", "--show-toplevel"])
    if not repo_root:
        info["source_tier"] = "FILE_ONLY_NO_GIT_PROVENANCE"
        return info

    rel_path = os.path.relpath(script_path, repo_root)
    info["repo_root"] = repo_root
    info["repo_relpath"] = rel_path
    info["head_sha"] = core.git_text(repo_root, ["rev-parse", "HEAD"])
    info["branch"] = core.git_text(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    info["remote_origin"] = core.git_text(repo_root, ["remote", "get-url", "origin"])

    status = core.git_text(repo_root, ["status", "--short", "--", rel_path])
    if status:
        info["git_status"] = status
        info["source_tier"] = "LOCAL_WORKTREE_DIRTY"
    else:
        info["git_status"] = "clean"
        info["source_tier"] = "GIT_TRACKED_CLEAN"

    return info


def read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"prompt": raw}
    except Exception:
        return {"prompt": raw}


def resolve_gemini_binary(config: dict[str, Any]) -> str | None:
    configured = str(config.get("geminiBinary") or "").strip()
    candidates = [
        configured,
        "/opt/homebrew/bin/gemini",
        os.path.expanduser("~/.local/bin/gemini"),
        "gemini",
    ]

    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isabs(candidate) and os.path.exists(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def build_cli_prompt(messages: list[dict[str, str]], model: str) -> str:
    system = messages[0]["content"]
    user = messages[1]["content"]
    return (
        "[ACP Gemini CLI Critic Lane]\n"
        f"Runtime: gemini CLI\n"
        f"Model: {model}\n"
        "Mode: read-only plan audit. Do not edit files. Do not run tools unless explicitly permitted.\n"
        "You are comparing claims against objective evidence. Return the required JSON block first.\n\n"
        "--- System Contract ---\n"
        f"{system}\n\n"
        "--- User Evidence Packet ---\n"
        f"{user}"
    )


def run_gemini_cli(prompt: str, config: dict[str, Any], workspace_root: str | None) -> dict[str, Any]:
    gemini_bin = resolve_gemini_binary(config)
    if not gemini_bin:
        return {"text": None, "error": "gemini CLI not found", "exit_code": 127}

    model = str(config.get("model") or DEFAULT_MODEL)
    command = [
        gemini_bin,
        "--approval-mode",
        "plan",
        "--model",
        model,
        "--prompt",
        prompt,
        "--output-format",
        "text",
    ]
    if config.get("geminiUseAcpFlag", False):
        command.insert(1, "--acp")

    env = os.environ.copy()
    path_prefix = ["/opt/homebrew/bin", os.path.expanduser("~/.local/bin")]
    existing_path = env.get("PATH", "")
    env["PATH"] = ":".join([p for p in path_prefix if os.path.isdir(p)] + [existing_path])

    cwd = workspace_root if workspace_root and Path(workspace_root).is_dir() else os.getcwd()
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=int(config.get("geminiTimeoutSeconds", DEFAULT_TIMEOUT_SECONDS)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "text": None,
            "error": "TIMEOUT",
            "exit_code": -1,
            "elapsed": int(config.get("geminiTimeoutSeconds", DEFAULT_TIMEOUT_SECONDS)),
            "command": command,
            "model": model,
        }

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    text = stdout or stderr
    return {
        "text": text if text else None,
        "stderr": stderr[:4000] if stderr else "",
        "exit_code": result.returncode,
        "elapsed": round(time.monotonic() - started, 1),
        "command": command,
        "model": model,
    }


def emit(additional_context: str | None) -> int:
    payload = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit"}}
    if additional_context:
        payload["hookSpecificOutput"]["additionalContext"] = additional_context
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def main() -> int:
    config = load_config()
    data = read_stdin_json()
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return emit(None)

    dry_run = bool(data.get("dry_run")) or os.environ.get("VORTEX_CRITIC_DRY_RUN") == "1"

    preset_name = config.get("preset", "渦")
    coordinate = core.resolve_preset(preset_name)
    if "pcc_coordinate" in data:
        coordinate = str(data["pcc_coordinate"])
        preset_name = "custom"
    if "pcc_overrides" in data:
        coordinate = core.apply_overrides(coordinate, str(data["pcc_overrides"]))

    pcc_stack = core.build_pcc_stack(data, config, coordinate, preset_name)
    stack_label = " > ".join(f"{layer['name']}:{layer['coordinate']}" for layer in pcc_stack)

    workspace_root = data.get("workspaceRoot", "")
    evidence: dict[str, Any] = {"critic_source": collect_script_provenance()}
    if workspace_root and Path(workspace_root).is_dir():
        evidence.update(core.collect_git_evidence(
            workspace_root,
            int(config.get("diffPatchChars", core.DEFAULT_CONFIG["diffPatchChars"])),
        ))
        evidence.update(core.collect_test_evidence(workspace_root))

    core.attach_passthrough_evidence(
        data,
        evidence,
        int(config.get("passthroughEvidenceChars", core.DEFAULT_CONFIG["passthroughEvidenceChars"])),
    )
    evidence["evidence_quality"] = core.summarize_evidence_quality(evidence)

    if dry_run and not data.get("dry_run_include_rag"):
        rag_context = {"enabled": False, "providers": [], "packets": [], "dry_run_skipped": True}
    else:
        rag_context = core.retrieve_all_rag_context(data, config, evidence, pcc_stack)

    messages = core.build_messages(data, config, evidence, coordinate, preset_name, pcc_stack, rag_context)
    cli_prompt = build_cli_prompt(messages, str(config.get("model") or DEFAULT_MODEL))

    if dry_run:
        dry_payload = {
            "script_version": SCRIPT_VERSION,
            "model": str(config.get("model") or DEFAULT_MODEL),
            "coordinate": coordinate,
            "preset": preset_name,
            "pcc_stack": stack_label,
            "critic_source": evidence.get("critic_source"),
            "evidence_quality": evidence.get("evidence_quality"),
            "rag": {
                "enabled": rag_context.get("enabled"),
                "dry_run_skipped": rag_context.get("dry_run_skipped", False),
                "packet_count": len(rag_context.get("packets", []) or []),
            },
            "prompt_chars": len(cli_prompt),
            "prompt_preview": cli_prompt[:2400],
        }
        return emit("[Gemini CLI Critic Dry Run | no Gemini call]\n" + json.dumps(dry_payload, ensure_ascii=False, indent=2))

    result = run_gemini_cli(cli_prompt, config, workspace_root if isinstance(workspace_root, str) else None)
    if result.get("text") is None:
        print(f"Gemini CLI critic error: {result.get('error', 'no response')}", file=sys.stderr)
        return emit(None)

    response_text = str(result["text"])
    try:
        core.save_ft_record(
            config=config,
            coordinate=coordinate,
            preset_name=str(preset_name),
            pcc_stack=pcc_stack,
            rag_context=rag_context,
            evidence=evidence,
            messages=messages,
            response_text=response_text,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            model=str(result.get("model", config.get("model", DEFAULT_MODEL))),
        )
    except Exception as exc:
        print(f"Gemini CLI FT save failed: {exc}", file=sys.stderr)

    additional_context = (
        f"[Gemini CLI Critic | model:{result.get('model')} | PCC Stack:{stack_label} | "
        f"exit:{result.get('exit_code')} | elapsed:{result.get('elapsed')}s]\n"
        f"{response_text.strip()}"
    )
    return emit(additional_context)


if __name__ == "__main__":
    raise SystemExit(main())
