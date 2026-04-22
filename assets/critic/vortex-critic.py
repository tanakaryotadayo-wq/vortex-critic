#!/usr/bin/env python3
"""
vortex-critic.py — VORTEX Protocol DeepSeek Critic Hook (v2.0)

Real PCC 9-axis coordinate engine + evidence-based verification.
Designed for Fine-Tuning data collection.

PCC axes (from Newgate fusion_gate.py canonical spec):
  I=Initiative  F=Focus  C=Context  B=Boldness  R=Resistance
  M=Memory      E=Emotion  N=Number  S=Session

Evidence principle:
  "workerの自己申告は信用しない。
   git diff、tests、lint、exit code、changed files で判定する。
   スコープ外変更は落とす。"

Usage:
  1. VS Code workspace hook (.copilot/hooks/)
  2. Standalone: echo '{"prompt":"...","workspaceRoot":"/path"}' | python3 vortex-critic.py
  3. MCP umpire (called from fusion-orchestrator-mcp)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════════════════════
# PCC 9-Axis Coordinate Engine (canonical, from Newgate fusion_gate.py)
# ═══════════════════════════════════════════════════════════════════════════════

PCC_AXES = ["I", "F", "C", "B", "R", "M", "E", "N", "S"]

PCC_MEANINGS = {
    "I": {1: "確認を最優先", 9: "直ちに躊躇なく実行"},
    "F": {1: "広く浅い視点", 9: "一点集中、脱線禁止"},
    "C": {1: "文脈無視", 9: "文脈完全維持"},
    "B": {1: "保守的", 9: "創造性最大化"},
    "R": {1: "同調", 9: "迎合厳禁"},
    "M": {1: "過去無視", 9: "過去最大活用"},
    "E": {1: "感情完全排除", 9: "感情許容"},
    "N": {1: "結論1つ", 9: "複数網羅的列挙"},
    "S": {1: "新規扱い", 9: "前の続き"},
}

# Presets: 9-digit coordinate strings
# Original Newgate presets + new auditor/reviewer presets
PCC_PRESETS = {
    # ── Originals (from fusion_gate.py) ──
    "#極": "998598118",  # 極限: 即実行、一点集中、保守的→高創造、迎合禁止
    "#探": "525955895",  # 探索: バランス、文脈維持、迎合禁止、網羅列挙
    "#均": "555555555",  # 均衡: 全軸ニュートラル
    "#静": "385255215",  # 静観: 確認優先、広い視点、保守的、感情排除
    "#動": "957855595",  # 動的: 即実行、一点集中、高文脈、迎合禁止

    # ── New: Auditor/Reviewer presets ──
    "#監": "195199115",  # 監査: 確認最優先、一点集中、保守的、迎合厳禁、過去最大活用、感情排除、結論1つ
    "#刃": "995599118",  # 刃: 即実行、一点集中、中文脈、迎合禁止、感情排除、結論1つ、継続
    "#渦": "199199119",  # 渦(VORTEX): 確認最優先、集中、全文脈、保守的、迎合厳禁、全記憶、感情排除、結論1つ、継続
}


def parse_pcc_coordinate(coord_str: str) -> list[int]:
    """Parse a 9-digit coordinate string into axis values."""
    if len(coord_str) != 9 or not coord_str.isdigit():
        return [5] * 9  # default neutral
    return [int(c) for c in coord_str]


def resolve_preset(name: str) -> str:
    """Resolve preset name to 9-digit coordinate. Accepts with/without #."""
    key = name if name.startswith("#") else f"#{name}"
    return PCC_PRESETS.get(key, "555555555")


def apply_overrides(coord: str, override_text: str) -> str:
    """Apply d-overrides like 'd5:3 d9:1' to a coordinate string."""
    import re
    coord_list = list(coord)
    for idx_str, val_str in re.findall(r"d(\d):(\d)", override_text):
        idx = int(idx_str) - 1
        if 0 <= idx < 9:
            coord_list[idx] = val_str
    return "".join(coord_list)


def generate_constraints(coord: str) -> list[str]:
    """Generate human-readable constraint strings from a coordinate.

    Only extreme values (≤2 or ≥8) produce constraints.
    This matches Newgate's fusion_gate.py behavior exactly.
    """
    constraints = []
    for i, val_str in enumerate(coord):
        val = int(val_str)
        axis = PCC_AXES[i]
        if val <= 2:
            constraints.append(PCC_MEANINGS[axis][1])
        elif val >= 8:
            constraints.append(PCC_MEANINGS[axis][9])
    return constraints


def format_coordinate_header(coord: str, preset_name: str = "") -> str:
    """Format a coordinate into a human-readable header block."""
    axes = parse_pcc_coordinate(coord)
    lines = [f"[PCC Coordinate: {coord}]"]
    if preset_name:
        lines[0] += f" (preset: {preset_name})"
    lines.append("Axis values: " + " ".join(
        f"{PCC_AXES[i]}={axes[i]}" for i in range(9)
    ))
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# VORTEX Evidence Collection
# ═══════════════════════════════════════════════════════════════════════════════

def collect_git_evidence(workspace_root: str) -> dict[str, Any]:
    """Collect objective evidence from git state."""
    evidence: dict[str, Any] = {}

    commands = {
        "diff_stat": ["git", "diff", "--stat", "HEAD"],
        "changed_files": ["git", "diff", "--name-only", "HEAD"],
        "staged_files": ["git", "diff", "--name-only", "--cached"],
        "last_commit": ["git", "log", "-1", "--oneline"],
        "branch": ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        "diff_patch": ["git", "diff", "HEAD", "--no-color", "-U3"],
    }

    for key, cmd in commands.items():
        try:
            result = subprocess.run(
                cmd, cwd=workspace_root, capture_output=True,
                text=True, timeout=5, check=False
            )
            if result.returncode == 0 and result.stdout.strip():
                val = result.stdout.strip()
                if key in ("changed_files", "staged_files"):
                    evidence[key] = val.split("\n")
                elif key == "diff_patch":
                    # Cap diff size for prompt budget
                    evidence[key] = val[:8000]
                else:
                    evidence[key] = val
        except Exception:
            pass

    return evidence


def collect_test_evidence(workspace_root: str) -> dict[str, Any]:
    """Check for recent test results or common test artifacts."""
    evidence: dict[str, Any] = {}
    ws = Path(workspace_root)

    test_artifacts = [
        "test-results.xml", "junit.xml", ".pytest_cache/lastfailed",
        "coverage/lcov.info", "coverage/coverage-summary.json",
    ]
    found = []
    for artifact in test_artifacts:
        if (ws / artifact).exists():
            found.append(artifact)
    if found:
        evidence["test_artifacts_found"] = found

    return evidence


# ═══════════════════════════════════════════════════════════════════════════════
# Config & API Key Resolution
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "enabled": True,
    "provider": "deepseek",
    "model": "deepseek-chat",
    "preset": "渦",
    "baseUrl": "https://api.deepseek.com/chat/completions",
    "keychainService": "deepseek-api",
    "keychainAccount": "default",
    "fallbackKeychainServices": ["deepseek-api", "deepseek", "DeepSeek API"],
    "maxTokens": 1200,
    "temperature": 0.1,
    "promptContextChars": 16000,
    "responseChars": 4000,
    "styleNote": "Return critique in Japanese. Be dense, concrete, and merciless about missing evidence.",
    "focusAreas": [
        "completion illusion detection",
        "missing verification evidence",
        "git diff vs claimed changes mismatch",
        "test/lint exit code verification",
        "scope violation detection",
    ],
    # FT data collection
    "ft_data_dir": os.path.expanduser("~/.vortex-critic"),
    "ft_collect": True,
}


def load_config() -> dict[str, Any]:
    config_path = Path(__file__).with_name("vortex-critic.config.json")
    if not config_path.exists():
        config_path = Path(__file__).with_name("deepseek-critic.config.json")
    if not config_path.exists():
        return dict(DEFAULT_CONFIG)

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_CONFIG)
        merged.update(data)
        return merged
    except Exception:
        print("VORTEX critic config JSON is invalid.", file=sys.stderr)
        return dict(DEFAULT_CONFIG)


def read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {"prompt": raw}


def read_keychain_secret(service: str, account: str | None) -> str | None:
    base = ["security", "find-generic-password", "-w", "-s", service]
    variants = []
    if account:
        variants.append(base + ["-a", account])
    variants.append(base)

    for cmd in variants:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=4, check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            continue
    return None


def resolve_api_key(config: dict[str, Any]) -> str | None:
    env_key = os.environ.get("DEEPSEEK_API_KEY")
    if env_key:
        return env_key

    service = config.get("keychainService")
    account = config.get("keychainAccount")
    if isinstance(service, str) and service:
        secret = read_keychain_secret(service, account if isinstance(account, str) else None)
        if secret:
            return secret

    for fallback_service in config.get("fallbackKeychainServices", []):
        if not isinstance(fallback_service, str):
            continue
        secret = read_keychain_secret(fallback_service, account if isinstance(account, str) else None)
        if secret:
            return secret

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Prompt Construction (PCC-aware)
# ═══════════════════════════════════════════════════════════════════════════════

def build_messages(
    data: dict[str, Any],
    config: dict[str, Any],
    evidence: dict[str, Any],
    coordinate: str,
    preset_name: str,
) -> list[dict[str, str]]:
    """Build DeepSeek messages with real PCC coordinate injection."""

    constraints = generate_constraints(coordinate)
    constraint_block = "\n".join(f"- {c}" for c in constraints) if constraints else "- (neutral coordinate, no extreme constraints)"
    style_note = config.get("styleNote", DEFAULT_CONFIG["styleNote"])
    focus_areas = "\n".join(f"- {item}" for item in config.get("focusAreas", []))

    prompt = str(data.get("prompt", ""))
    active_file = data.get("activeFile")
    workspace_root = data.get("workspaceRoot")
    focus_block = f"Focus Areas:\n{focus_areas}\n" if focus_areas else ""

    # ── System Prompt: PCC + VORTEX ──
    system_prompt = (
        f"{format_coordinate_header(coordinate, preset_name)}\n\n"

        "[VORTEX Evidence Verification Protocol]\n"
        "You are a read-only critic and evidence auditor for another coding agent.\n"
        "You do not implement the task. You verify claims against objective evidence.\n\n"

        "Core Principle:\n"
        "- workerの自己申告は信用しない\n"
        "- git diff、tests、lint、exit code、changed files で判定する\n"
        "- スコープ外変更は落とす\n\n"

        f"PCC Behavioral Constraints (auto-generated from coordinate {coordinate}):\n"
        f"{constraint_block}\n"
        f"- {style_note}\n\n"

        f"{focus_block}"

        "\nRequired Output (structured JSON for FT collection):\n"
        "```json\n"
        "{\n"
        '  "verdict": "VERIFIED | UNVERIFIED | UNVERIFIED_MUTATION",\n'
        '  "confidence": 0.0-1.0,\n'
        '  "evidence_found": ["list of objective evidence items"],\n'
        '  "evidence_missing": ["list of what should exist but does not"],\n'
        '  "scope_violations": ["files changed outside intended scope"],\n'
        '  "findings": ["specific issues found"],\n'
        '  "next_action": "concrete verification command to run",\n'
        '  "reasoning": "brief explanation of verdict"\n'
        "}\n"
        "```\n"
        "After the JSON block, provide a brief Japanese summary (3-5 lines).\n"
    )

    # ── User Prompt: evidence + task ──
    context_lines = []
    if isinstance(active_file, str) and active_file:
        context_lines.append(f"Active file: {active_file}")
    if isinstance(workspace_root, str) and workspace_root:
        context_lines.append(f"Workspace root: {workspace_root}")

    # Inject objective evidence
    if evidence:
        context_lines.append("\n--- Objective Evidence (collected by VORTEX, NOT self-reported) ---")
        if "branch" in evidence:
            context_lines.append(f"Branch: {evidence['branch']}")
        if "diff_stat" in evidence:
            context_lines.append(f"Git diff stat:\n{evidence['diff_stat']}")
        if "changed_files" in evidence:
            context_lines.append(f"Changed files: {', '.join(evidence['changed_files'])}")
        if "staged_files" in evidence:
            context_lines.append(f"Staged files: {', '.join(evidence['staged_files'])}")
        if "last_commit" in evidence:
            context_lines.append(f"Last commit: {evidence['last_commit']}")
        if "diff_patch" in evidence:
            context_lines.append(f"Diff patch (truncated):\n```diff\n{evidence['diff_patch']}\n```")
        if "test_artifacts_found" in evidence:
            context_lines.append(f"Test artifacts found: {', '.join(evidence['test_artifacts_found'])}")
    else:
        context_lines.append("⚠️ NO OBJECTIVE EVIDENCE FOUND. Worker has not run any verification.")

    # Pass-through exit codes
    if "test_exit_code" in data:
        context_lines.append(f"Test exit code: {data['test_exit_code']}")
    if "lint_exit_code" in data:
        context_lines.append(f"Lint exit code: {data['lint_exit_code']}")
    if "scope_files" in data:
        context_lines.append(f"Intended scope: {', '.join(data['scope_files'])}")

    context_block = "\n".join(context_lines)

    user_prompt = (
        "Review the following AI agent output as a VORTEX auditor.\n"
        "Do not answer the original request. Only verify whether claims are backed by evidence.\n"
        "If the agent claims 'done' without test/lint evidence, mark UNVERIFIED.\n\n"
        f"{context_block}\n\n"
        f"--- Agent Output to Audit ---\n"
        f"{prompt[: int(config.get('promptContextChars', DEFAULT_CONFIG['promptContextChars']))]}"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# DeepSeek API Call
# ═══════════════════════════════════════════════════════════════════════════════

def call_deepseek(messages: list[dict[str, str]], config: dict[str, Any], api_key: str) -> Optional[dict]:
    """Call DeepSeek API and return structured result with usage stats."""
    payload = {
        "model": config.get("model", DEFAULT_CONFIG["model"]),
        "messages": messages,
        "temperature": config.get("temperature", DEFAULT_CONFIG["temperature"]),
        "max_tokens": config.get("maxTokens", DEFAULT_CONFIG["maxTokens"]),
        "stream": False,
    }
    request = urllib.request.Request(
        config.get("baseUrl", DEFAULT_CONFIG["baseUrl"]),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "text": None}
    except Exception as exc:
        return {"error": str(exc), "text": None}

    try:
        text = body["choices"][0]["message"]["content"]
        usage = body.get("usage", {})
        return {
            "text": text[:int(config.get("responseChars", DEFAULT_CONFIG["responseChars"]))],
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "model": body.get("model", config.get("model", "")),
        }
    except Exception:
        return {"error": "invalid response structure", "text": None}


# ═══════════════════════════════════════════════════════════════════════════════
# FT Data Collection
# ═══════════════════════════════════════════════════════════════════════════════

def save_ft_record(
    config: dict[str, Any],
    coordinate: str,
    preset_name: str,
    evidence: dict[str, Any],
    messages: list[dict[str, str]],
    response_text: str,
    usage: dict[str, int],
    model: str,
) -> None:
    """Save a structured FT training record to JSONL.

    Each line is a complete (system, user, assistant) triple with metadata,
    ready for SFT or DPO fine-tuning pipelines.
    """
    if not config.get("ft_collect", True):
        return

    ft_dir = Path(os.path.expanduser(config.get("ft_data_dir", DEFAULT_CONFIG["ft_data_dir"])))
    ft_dir.mkdir(parents=True, exist_ok=True)
    ft_path = ft_dir / "ft_data.jsonl"

    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "coordinate": coordinate,
        "preset": preset_name,
        "model": model,
        "usage": usage,

        # SFT training format (messages array)
        "messages": [
            messages[0],  # system
            messages[1],  # user
            {"role": "assistant", "content": response_text},  # target
        ],

        # Structured metadata for filtering/analysis
        "evidence_summary": {
            "has_diff": "diff_stat" in evidence or "diff_patch" in evidence,
            "has_tests": "test_artifacts_found" in evidence,
            "changed_file_count": len(evidence.get("changed_files", [])),
            "has_exit_codes": "test_exit_code" in evidence or "lint_exit_code" in evidence,
        },
    }

    try:
        with open(ft_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[VORTEX] FT record saved: {ft_path}", file=sys.stderr)
    except Exception as exc:
        print(f"[VORTEX] FT save failed: {exc}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════════
# Hook Output
# ═══════════════════════════════════════════════════════════════════════════════

def emit(additional_context: str | None) -> int:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
        }
    }
    if additional_context:
        payload["hookSpecificOutput"]["additionalContext"] = additional_context
    print(json.dumps(payload, ensure_ascii=False))
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    config = load_config()
    if not config.get("enabled", True):
        return emit(None)

    data = read_stdin_json()
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return emit(None)

    api_key = resolve_api_key(config)
    if not api_key:
        print("DeepSeek API key missing.", file=sys.stderr)
        return emit(None)

    # ── Resolve PCC Coordinate ──
    preset_name = config.get("preset", "渦")
    coordinate = resolve_preset(preset_name)

    # Allow runtime override via stdin data
    if "pcc_coordinate" in data:
        coordinate = str(data["pcc_coordinate"])
        preset_name = "custom"
    if "pcc_overrides" in data:
        coordinate = apply_overrides(coordinate, str(data["pcc_overrides"]))

    print(f"[VORTEX] PCC coordinate: {coordinate} (preset: {preset_name})", file=sys.stderr)
    print(f"[VORTEX] Constraints: {generate_constraints(coordinate)}", file=sys.stderr)

    # ── Collect Objective Evidence ──
    workspace_root = data.get("workspaceRoot", "")
    evidence: dict[str, Any] = {}
    if workspace_root and Path(workspace_root).is_dir():
        evidence.update(collect_git_evidence(workspace_root))
        evidence.update(collect_test_evidence(workspace_root))

    if "test_exit_code" in data:
        evidence["test_exit_code"] = data["test_exit_code"]
    if "lint_exit_code" in data:
        evidence["lint_exit_code"] = data["lint_exit_code"]

    # ── Build + Call ──
    messages = build_messages(data, config, evidence, coordinate, preset_name)
    result = call_deepseek(messages, config, api_key)

    if not result or result.get("text") is None:
        error = result.get("error", "no response") if result else "no response"
        print(f"VORTEX critic error: {error}", file=sys.stderr)
        return emit(None)

    response_text = result["text"]

    # ── FT Data Collection ──
    save_ft_record(
        config=config,
        coordinate=coordinate,
        preset_name=preset_name,
        evidence=evidence,
        messages=messages,
        response_text=response_text,
        usage=result.get("usage", {}),
        model=result.get("model", ""),
    )

    # ── Emit to Copilot Hook ──
    additional_context = (
        f"[VORTEX Critic | PCC:{coordinate}]\n"
        f"{response_text.strip()}"
    )
    return emit(additional_context)


if __name__ == "__main__":
    raise SystemExit(main())
