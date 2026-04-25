#!/usr/bin/env python3
"""
vortex-critic.py — VORTEX Protocol DeepSeek Critic Hook (v2.5)

ACP x PCC Context Stack + Real RAG + evidence-based verification.
Designed for Fine-Tuning data collection.

PCC axes (from Newgate fusion_gate.py canonical spec):
  I=Intent  F=Focus  C=Context  B=Balance  R=Resistance
  M=Memory  E=Emotion  N=Number  S=Sync

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
import hashlib
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

SCRIPT_VERSION = "2.5.0-real-evidence-untracked"
SCRIPT_NAME = "vortex-critic.py"
SCRIPT_TRUST_POLICY = (
    "Live git diff, exit codes, and current file hashes outrank RAG packets, "
    "embedded source_code, and agent self-reports."
)


# ═══════════════════════════════════════════════════════════════════════════════
# PCC 9-Axis Coordinate Engine (canonical, from Newgate fusion_gate.py)
# ═══════════════════════════════════════════════════════════════════════════════

PCC_AXES = ["I", "F", "C", "B", "R", "M", "E", "N", "S"]

ROOT_L0_COORDINATE = "999299119"
ROOT_L0_DIRECTIVE = (
    "Root baseline: absolute execution, context locked, zero emotion, "
    "no flattery, no hedging, no truncation, and no trust in self-report."
)

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

DEFAULT_PCC_STACK = [
    {
        "name": "root-l0",
        "role": "invariant baseline",
        "preset": "root",
        "coordinate": ROOT_L0_COORDINATE,
        "directive": ROOT_L0_DIRECTIVE,
    },
    {
        "name": "explorer",
        "role": "assumption explorer",
        "preset": "探",
        "directive": "Find hidden assumptions, missing alternatives, and unclear scope before judging.",
    },
    {
        "name": "evidence-auditor",
        "role": "objective evidence judge",
        "preset": "監",
        "directive": "Accept only git diff, changed files, test/lint exit codes, and concrete artifacts.",
    },
    {
        "name": "implementation-reviewer",
        "role": "regression and blast-radius reviewer",
        "preset": "刃",
        "directive": "Look for concrete implementation gaps, scope violations, and the safest next verification.",
    },
    {
        "name": "synthesizer",
        "role": "final VORTEX verdict composer",
        "preset": "$active",
        "directive": "Collapse all prior layers into one structured verdict with a short Japanese summary.",
    },
]

DEFAULT_RAG_DB_CANDIDATES = [
    os.path.join(os.path.expanduser("~"), "俺のフュージョンゲートまとめ", "neural_packets.db"),
    os.path.join(
        os.path.expanduser("~"),
        "俺のフュージョンゲートまとめ",
        "復活のフュージョンゲート",
        "neural_packets.db",
    ),
    os.path.join(os.path.expanduser("~"), "Newgate", "intelligence", "neural_packets.db"),
]

PATH_CONFIG_KEYS = {"ft_data_dir", "ragDbPath"}

TASK_EVIDENCE_KEYS = {
    "diff_stat",
    "changed_files",
    "staged_files",
    "status_short",
    "diff_patch",
    "untracked_files",
    "untracked_patch_skipped",
    "untracked_patch_truncated",
    "test_artifacts_found",
    "test_artifacts_detail",
    "test_exit_code",
    "lint_exit_code",
    "typecheck_exit_code",
    "security_exit_code",
    "verification_exit_code",
    "test_command",
    "lint_command",
    "typecheck_command",
    "security_command",
    "verification_command",
    "test_output",
    "lint_output",
    "typecheck_output",
    "security_output",
    "verification_log",
}

EXIT_CODE_KEYS = {
    "test_exit_code",
    "lint_exit_code",
    "typecheck_exit_code",
    "security_exit_code",
    "verification_exit_code",
}

TEXT_EVIDENCE_KEYS = {
    "test_output",
    "lint_output",
    "typecheck_output",
    "security_output",
    "verification_log",
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


def normalize_config_paths(config: dict[str, Any]) -> dict[str, Any]:
    """Expand user-relative paths loaded from JSON config."""
    for key in PATH_CONFIG_KEYS:
        value = config.get(key)
        if isinstance(value, str):
            config[key] = os.path.expanduser(value)
    return config


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def run_text_command(cmd: list[str], cwd: str | Path, timeout: int = 5) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return 999, "", str(exc)


def git_text(cwd: str | Path, args: list[str], timeout: int = 5) -> str | None:
    code, stdout, _stderr = run_text_command(["git", *args], cwd, timeout)
    if code == 0 and stdout:
        return stdout
    return None


def resolve_local_ref(ref: str) -> Path | None:
    """Resolve local:// and file:// refs without accepting remote URLs."""
    if ref.startswith("local://"):
        return Path(os.path.expanduser(ref[len("local://"):]))
    if ref.startswith("file://"):
        return Path(os.path.expanduser(ref[len("file://"):]))
    if ref.startswith("/") or ref.startswith("~"):
        return Path(os.path.expanduser(ref))
    return None


def collect_script_provenance() -> dict[str, Any]:
    """Record the live critic script source, so old packets cannot impersonate it."""
    script_path = Path(__file__).resolve()
    info: dict[str, Any] = {
        "name": SCRIPT_NAME,
        "version": SCRIPT_VERSION,
        "path": str(script_path),
        "trust_policy": SCRIPT_TRUST_POLICY,
        "sha256": sha256_file(script_path),
    }

    try:
        stat = script_path.stat()
        info["size_bytes"] = stat.st_size
        info["mtime_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime))
    except OSError:
        pass

    repo_root = git_text(script_path.parent, ["rev-parse", "--show-toplevel"])
    if not repo_root:
        info["source_tier"] = "FILE_ONLY_NO_GIT_PROVENANCE"
        return info

    rel_path = os.path.relpath(script_path, repo_root)
    info["repo_root"] = repo_root
    info["repo_relpath"] = rel_path
    info["head_sha"] = git_text(repo_root, ["rev-parse", "HEAD"])
    info["branch"] = git_text(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    info["remote_origin"] = git_text(repo_root, ["remote", "get-url", "origin"])

    status = git_text(repo_root, ["status", "--short", "--", rel_path])
    if status:
        info["git_status"] = status
        info["source_tier"] = "LOCAL_WORKTREE_DIRTY"
    else:
        info["git_status"] = "clean"
        info["source_tier"] = "GIT_TRACKED_CLEAN"

    return info


def resolve_layer_coordinate(
    layer: dict[str, Any],
    active_coordinate: str,
    active_preset_name: str,
) -> tuple[str, str]:
    """Resolve one PCC stack layer to a coordinate and preset label."""
    raw_preset = layer.get("preset")
    raw_coordinate = layer.get("coordinate")

    if raw_preset == "$active" or raw_coordinate == "$active":
        coordinate = active_coordinate
        preset_name = active_preset_name
    elif isinstance(raw_coordinate, str) and raw_coordinate.isdigit() and len(raw_coordinate) == 9:
        coordinate = raw_coordinate
        preset_name = str(raw_preset or ("root" if raw_coordinate == ROOT_L0_COORDINATE else "custom"))
    else:
        preset_name = str(raw_preset or active_preset_name)
        coordinate = resolve_preset(preset_name)

    if "overrides" in layer:
        coordinate = apply_overrides(coordinate, str(layer["overrides"]))

    if len(coordinate) != 9 or not coordinate.isdigit():
        coordinate = "555555555"
        preset_name = "neutral"

    return coordinate, preset_name


def build_pcc_stack(
    data: dict[str, Any],
    config: dict[str, Any],
    active_coordinate: str,
    active_preset_name: str,
) -> list[dict[str, Any]]:
    """Build the hidden multi-layer PCC control stack.

    This keeps the restored single-coordinate behavior as the final active
    synthesizer layer, while adding stable Root/Explorer/Auditor/Reviewer
    layers ahead of it.
    """
    raw_stack = data.get("pcc_stack") or config.get("pccStack") or DEFAULT_PCC_STACK
    if not isinstance(raw_stack, list):
        raw_stack = DEFAULT_PCC_STACK

    layers: list[dict[str, Any]] = []
    for index, raw_layer in enumerate(raw_stack, start=1):
        if isinstance(raw_layer, str):
            layer = {"preset": raw_layer}
        elif isinstance(raw_layer, dict):
            layer = dict(raw_layer)
        else:
            continue

        if layer.get("enabled", True) is False:
            continue

        coordinate, preset_name = resolve_layer_coordinate(
            layer, active_coordinate, active_preset_name
        )
        name = str(layer.get("name") or f"layer-{index}")
        role = str(layer.get("role") or "PCC control layer")
        directive = str(layer.get("directive") or "")
        constraints = generate_constraints(coordinate)

        layers.append({
            "name": name,
            "role": role,
            "preset": preset_name,
            "coordinate": coordinate,
            "directive": directive,
            "constraints": constraints,
            "axis_values": " ".join(
                f"{PCC_AXES[i]}={parse_pcc_coordinate(coordinate)[i]}"
                for i in range(9)
            ),
        })

    include_root = config.get("includeRootL0", True)
    has_root = any(layer["coordinate"] == ROOT_L0_COORDINATE for layer in layers)
    if include_root and not has_root:
        root_layer = {
            "name": "root-l0",
            "role": "invariant baseline",
            "preset": "root",
            "coordinate": ROOT_L0_COORDINATE,
            "directive": ROOT_L0_DIRECTIVE,
            "constraints": generate_constraints(ROOT_L0_COORDINATE),
            "axis_values": " ".join(
                f"{PCC_AXES[i]}={parse_pcc_coordinate(ROOT_L0_COORDINATE)[i]}"
                for i in range(9)
            ),
        }
        layers.insert(0, root_layer)

    if not layers:
        coordinate, preset_name = active_coordinate, active_preset_name
        layers.append({
            "name": "synthesizer",
            "role": "final VORTEX verdict composer",
            "preset": preset_name,
            "coordinate": coordinate,
            "directive": "Produce the final evidence-based verdict.",
            "constraints": generate_constraints(coordinate),
            "axis_values": " ".join(
                f"{PCC_AXES[i]}={parse_pcc_coordinate(coordinate)[i]}"
                for i in range(9)
            ),
        })

    max_layers = int(config.get("maxPccLayers", 6))
    return layers[:max(1, max_layers)]


def format_pcc_stack(stack: list[dict[str, Any]]) -> str:
    """Render the multi-layer PCC stack for the system prompt."""
    lines = [
        "[ACP x PCC Context Stack]",
        "These stable control layers steer the critic before the realtime user payload.",
        "Apply all layers together; do not treat any single layer as the whole task.",
        "",
    ]

    for index, layer in enumerate(stack, start=1):
        constraints = layer.get("constraints") or []
        constraint_text = "; ".join(constraints) if constraints else "neutral/no extreme constraints"
        lines.extend([
            f"Layer {index}: {layer['name']} | role={layer['role']}",
            f"Coordinate: {layer['coordinate']} (preset: {layer['preset']})",
            f"Axis values: {layer['axis_values']}",
            f"Active constraints: {constraint_text}",
        ])
        if layer.get("directive"):
            lines.append(f"Directive: {layer['directive']}")
        lines.append("")

    return "\n".join(lines).rstrip()


# ═══════════════════════════════════════════════════════════════════════════════
# VORTEX Evidence Collection
# ═══════════════════════════════════════════════════════════════════════════════

def build_untracked_patch(
    workspace_root: str,
    files: list[str],
    max_chars: int,
) -> tuple[str, list[dict[str, Any]], bool]:
    """Render small untracked text files as synthetic new-file diffs."""
    if max_chars <= 0:
        return "", [], bool(files)

    root = Path(workspace_root).resolve()
    chunks: list[str] = []
    skipped: list[dict[str, Any]] = []
    remaining = max_chars
    truncated = False

    for rel_path in files:
        if remaining <= 0:
            truncated = True
            break

        rel_path = rel_path.strip()
        if not rel_path:
            continue

        path = (root / rel_path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            skipped.append({"path": rel_path, "reason": "outside workspace"})
            continue

        if not path.is_file():
            skipped.append({"path": rel_path, "reason": "not a regular file"})
            continue

        try:
            stat = path.stat()
        except OSError as exc:
            skipped.append({"path": rel_path, "reason": f"stat failed: {exc}"})
            continue

        if stat.st_size > 1024 * 1024:
            skipped.append({"path": rel_path, "reason": "too large", "size_bytes": stat.st_size})
            continue

        try:
            raw = path.read_bytes()
        except OSError as exc:
            skipped.append({"path": rel_path, "reason": f"read failed: {exc}"})
            continue

        if b"\0" in raw[:8192]:
            skipped.append({"path": rel_path, "reason": "binary-looking file", "size_bytes": stat.st_size})
            continue

        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        body = "".join(f"+{line}\n" for line in lines)
        header = (
            f"diff --git a/{rel_path} b/{rel_path}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{rel_path}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n"
        )
        chunk = header + body
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            truncated = True
            break
        chunks.append(chunk)
        remaining -= len(chunk)

    return "\n".join(chunks), skipped, truncated


def collect_git_evidence(workspace_root: str, diff_patch_chars: int = 12000) -> dict[str, Any]:
    """Collect objective evidence from git state."""
    evidence: dict[str, Any] = {}

    commands = {
        "repo_root": ["git", "rev-parse", "--show-toplevel"],
        "head_sha": ["git", "rev-parse", "HEAD"],
        "diff_stat": ["git", "diff", "--stat", "HEAD"],
        "changed_files": ["git", "diff", "--name-only", "HEAD"],
        "staged_files": ["git", "diff", "--name-only", "--cached"],
        "untracked_files": ["git", "ls-files", "--others", "--exclude-standard"],
        "status_short": ["git", "status", "--short"],
        "last_commit": ["git", "log", "-1", "--oneline"],
        "branch": ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        "diff_patch": ["git", "diff", "HEAD", "--no-color", "-U3"],
    }

    errors: list[str] = []
    for key, cmd in commands.items():
        code, stdout, stderr = run_text_command(cmd, workspace_root)
        if code == 0 and stdout:
            if key in ("changed_files", "staged_files", "untracked_files"):
                evidence[key] = stdout.split("\n")
            elif key == "diff_patch":
                evidence["diff_patch_full_chars"] = len(stdout)
                evidence["diff_patch_truncated"] = len(stdout) > diff_patch_chars
                evidence[key] = stdout[:diff_patch_chars]
            else:
                evidence[key] = stdout
        elif code != 0 and stderr:
            errors.append(f"{key}: {stderr[:300]}")

    if errors:
        evidence["git_errors"] = errors[:5]

    untracked_files = evidence.get("untracked_files")
    if isinstance(untracked_files, list) and untracked_files:
        changed_files = evidence.get("changed_files")
        if not isinstance(changed_files, list):
            changed_files = []
        evidence["changed_files"] = list(dict.fromkeys(changed_files + untracked_files))

        existing_patch = str(evidence.get("diff_patch") or "")
        if existing_patch:
            reserve_chars = min(max(4000, diff_patch_chars // 3), diff_patch_chars)
            tracked_budget = max(0, diff_patch_chars - reserve_chars)
            if len(existing_patch) > tracked_budget:
                existing_patch = existing_patch[:tracked_budget]
                evidence["diff_patch_truncated"] = True
        remaining_chars = max(0, diff_patch_chars - len(existing_patch))
        untracked_patch, skipped, truncated = build_untracked_patch(
            workspace_root,
            untracked_files,
            remaining_chars,
        )
        if untracked_patch:
            separator = "\n\n" if existing_patch else ""
            evidence["diff_patch"] = existing_patch + separator + untracked_patch
            evidence["diff_patch_full_chars"] = len(evidence["diff_patch"])
        if skipped:
            evidence["untracked_patch_skipped"] = skipped[:20]
        if truncated:
            evidence["untracked_patch_truncated"] = True
            evidence["diff_patch_truncated"] = True

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
    details = []
    for artifact in test_artifacts:
        path = ws / artifact
        if path.exists():
            found.append(artifact)
            try:
                stat = path.stat()
                details.append({
                    "path": artifact,
                    "size_bytes": stat.st_size,
                    "mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
                })
            except OSError:
                details.append({"path": artifact})
    if found:
        evidence["test_artifacts_found"] = found
        evidence["test_artifacts_detail"] = details

    return evidence


def attach_passthrough_evidence(
    data: dict[str, Any],
    evidence: dict[str, Any],
    max_chars: int,
) -> None:
    """Attach caller-supplied command logs and exit codes as evidence, capped."""
    for key in EXIT_CODE_KEYS:
        if key in data:
            evidence[key] = data[key]

    command_keys = {
        "test_command",
        "lint_command",
        "typecheck_command",
        "security_command",
        "verification_command",
    }
    for key in command_keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            evidence[key] = value.strip()[:max_chars]

    for key in TEXT_EVIDENCE_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            evidence[key] = value.strip()[:max_chars]


def summarize_evidence_quality(evidence: dict[str, Any]) -> dict[str, Any]:
    changed_files = evidence.get("changed_files")
    if not isinstance(changed_files, list):
        changed_files = []

    exit_codes = {
        key: evidence.get(key)
        for key in EXIT_CODE_KEYS
        if key in evidence
    }
    failing_exit_codes = {
        key: value for key, value in exit_codes.items()
        if str(value) not in ("0", "0.0", "None", "")
    }

    has_git_state = any(key in evidence for key in ("repo_root", "head_sha", "branch", "last_commit"))
    has_diff = bool(evidence.get("diff_stat") or evidence.get("diff_patch"))
    has_status = bool(evidence.get("status_short"))
    has_untracked = bool(evidence.get("untracked_files"))
    has_test_artifacts = bool(evidence.get("test_artifacts_found"))
    has_exit_codes = bool(exit_codes)
    has_logs = any(key in evidence for key in TEXT_EVIDENCE_KEYS)
    has_verification = has_test_artifacts or has_exit_codes or has_logs

    missing: list[str] = []
    flags: list[str] = []
    if not has_git_state:
        missing.append("git workspace provenance")
        flags.append("NO_GIT_PROVENANCE")
    if changed_files and not has_verification:
        missing.append("test/lint/typecheck exit code or verification log for changed files")
        flags.append("CHANGED_FILES_WITHOUT_VERIFICATION")
    if not changed_files and not has_diff and not has_status:
        flags.append("NO_WORKSPACE_MUTATION_DETECTED")
    if evidence.get("diff_patch_truncated"):
        flags.append("DIFF_PATCH_TRUNCATED")
    if has_untracked and evidence.get("untracked_patch_truncated"):
        flags.append("UNTRACKED_PATCH_TRUNCATED")
    if evidence.get("untracked_patch_skipped"):
        flags.append("UNTRACKED_PATCH_SKIPPED")
    if failing_exit_codes:
        flags.append("FAILING_EXIT_CODE")

    if failing_exit_codes:
        level = "FAIL"
    elif changed_files and has_verification:
        level = "VERIFIABLE_MUTATION"
    elif changed_files:
        level = "UNVERIFIED_MUTATION"
    elif has_git_state and has_verification:
        level = "VERIFIABLE_NO_DIFF"
    elif has_git_state:
        level = "GIT_STATE_ONLY"
    else:
        level = "NO_OBJECTIVE_EVIDENCE"

    return {
        "level": level,
        "has_git_state": has_git_state,
        "has_diff": has_diff,
        "has_status": has_status,
        "has_untracked": has_untracked,
        "has_test_artifacts": has_test_artifacts,
        "has_exit_codes": has_exit_codes,
        "has_logs": has_logs,
        "changed_file_count": len(changed_files),
        "exit_codes": exit_codes,
        "failing_exit_codes": failing_exit_codes,
        "missing": missing,
        "flags": flags,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Real RAG: BGE-M3-lite (:8094) + Qwen3-deep (:8093) + Neural Packets
# ═══════════════════════════════════════════════════════════════════════════════

RAG_JA_KEYWORDS = {
    "パケット": "packet",
    "ニューラル": "neural",
    "座標": "coordinate",
    "監査": "audit",
    "証拠": "evidence",
    "圧縮": "compression",
    "キャッシュ": "cache",
    "メモリ": "memory",
    "文脈": "context",
    "検索": "search",
    "埋め込み": "embedding",
    "ラグ": "rag",
}

RAG_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "have",
    "been", "done", "true", "false", "none", "null", "will", "should",
}


def resolve_rag_db_path(config: dict[str, Any]) -> str | None:
    """Resolve the Neural Packets DB path without creating empty SQLite files."""
    candidates: list[str] = []

    configured = config.get("ragDbPath")
    if isinstance(configured, str) and configured:
        candidates.append(os.path.expanduser(configured))

    env_path = os.environ.get("EMBEDDING2_DB_PATH")
    if env_path:
        candidates.append(os.path.expanduser(env_path))

    candidates.extend(DEFAULT_RAG_DB_CANDIDATES)

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            if os.path.getsize(candidate) > 1024 * 1024:
                return candidate
        except OSError:
            continue
    return None


def extract_rag_terms(text: str, limit: int = 12) -> list[str]:
    """Extract compact search terms for content_index candidate retrieval."""
    terms: list[str] = []
    lowered_seen: set[str] = set()

    def add(term: str) -> None:
        cleaned = term.strip(" \t\r\n\"'`.,:;()[]{}<>")
        if len(cleaned) < 2:
            return
        key = cleaned.lower()
        if key in RAG_STOPWORDS or key in lowered_seen:
            return
        lowered_seen.add(key)
        terms.append(cleaned)

    for ja, en in RAG_JA_KEYWORDS.items():
        if ja in text:
            add(ja)
            add(en)

    for token in re.findall(r"[A-Za-z][A-Za-z0-9_./:-]{2,}", text):
        add(token)

    # Bias toward protocol terms if present; they are the useful cache keys here.
    priority = {"ACP", "PCC", "VORTEX", "RAG", "DeepSeek", "Qwen3", "packet", "cache", "memory"}
    terms.sort(key=lambda item: (item not in priority, -len(item), item.lower()))
    return terms[:limit]


def build_rag_query(
    data: dict[str, Any],
    evidence: dict[str, Any],
    pcc_stack: list[dict[str, Any]],
) -> str:
    """Build a retrieval query from the agent prompt plus local evidence shape."""
    parts: list[str] = []
    prompt = str(data.get("prompt", ""))
    if prompt:
        parts.append(prompt[:1800])

    active_file = data.get("activeFile")
    if isinstance(active_file, str) and active_file:
        parts.append(f"active_file: {active_file}")

    changed_files = evidence.get("changed_files")
    if isinstance(changed_files, list) and changed_files:
        parts.append("changed_files: " + " ".join(str(item) for item in changed_files[:20]))

    stack_label = " ".join(
        f"{layer['name']} {layer['coordinate']} {layer['preset']}" for layer in pcc_stack
    )
    parts.append(f"ACP PCC VORTEX Neural Packet RAG context stack {stack_label}")
    return "\n".join(parts)[:3000]


def rag_provider_value(
    provider: dict[str, Any] | None,
    provider_key: str,
    config: dict[str, Any],
    config_key: str,
    default: Any,
) -> Any:
    if provider and provider.get(provider_key) is not None:
        return provider[provider_key]
    return config.get(config_key, default)


def rag_embed_texts(
    texts: list[str],
    config: dict[str, Any],
    provider: dict[str, Any] | None = None,
) -> list[list[float]]:
    """Call an OpenAI-compatible embedding endpoint."""
    if not texts:
        return []

    payload = json.dumps({
        "input": texts,
        "model": rag_provider_value(
            provider, "embedModel", config, "ragEmbedModel", DEFAULT_CONFIG["ragEmbedModel"]
        ),
    }).encode("utf-8")
    request = urllib.request.Request(
        str(rag_provider_value(
            provider, "embedUrl", config, "ragEmbedUrl", DEFAULT_CONFIG["ragEmbedUrl"]
        )),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        timeout = int(rag_provider_value(
            provider, "timeoutSeconds", config, "ragTimeoutSeconds", DEFAULT_CONFIG["ragTimeoutSeconds"]
        ))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        return [item["embedding"] for item in body.get("data", []) if isinstance(item, dict)]
    except Exception as exc:
        print(f"[VORTEX] RAG embedding failed: {exc}", file=sys.stderr)
        return []


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def lexical_rag_score(text: str, terms: list[str]) -> float:
    lowered = text.lower()
    if not terms:
        return 0.0
    return sum(1 for term in terms if term.lower() in lowered) / len(terms)


def fetch_rag_candidates(
    conn: sqlite3.Connection,
    terms: list[str],
    candidate_limit: int,
) -> list[dict[str, Any]]:
    """Fetch candidate packet texts from Semantic Delta content_index."""
    if not terms:
        return []

    per_term_limit = max(8, candidate_limit // max(1, min(len(terms), 8)))
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []

    for term in terms:
        like = f"%{term}%"
        rows = conn.execute(
            """
            SELECT ci.packet_id, ci.canonical_text, p.status
            FROM content_index ci
            LEFT JOIN packets p ON p.id = ci.packet_id
            WHERE ci.packet_id LIKE ? COLLATE NOCASE
               OR ci.canonical_text LIKE ? COLLATE NOCASE
            LIMIT ?
            """,
            (like, like, per_term_limit),
        ).fetchall()
        for row in rows:
            packet_id = str(row["packet_id"])
            if packet_id in seen:
                continue
            seen.add(packet_id)
            candidates.append({
                "id": packet_id,
                "canonical_text": str(row["canonical_text"] or ""),
                "status": str(row["status"] or ""),
            })
            if len(candidates) >= candidate_limit:
                return candidates

    return candidates


def fetch_packet_payloads(conn: sqlite3.Connection, packet_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not packet_ids:
        return {}
    placeholders = ",".join("?" for _ in packet_ids)
    rows = conn.execute(
        f"SELECT id, data FROM packets WHERE id IN ({placeholders})",
        packet_ids,
    ).fetchall()

    payloads: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            parsed = json.loads(row["data"]) if row["data"] else {}
            payloads[str(row["id"])] = parsed if isinstance(parsed, dict) else {}
        except Exception:
            payloads[str(row["id"])] = {}
    return payloads


def inspect_packet_source_quality(payload: dict[str, Any], skill: dict[str, Any]) -> dict[str, Any]:
    """Detect stale embedded packet source without trusting it as canonical."""
    source_code = payload.get("source_code")
    code_ref = skill.get("code_ref") if isinstance(skill, dict) else ""
    source_code_text = source_code if isinstance(source_code, str) else ""
    code_ref_text = code_ref if isinstance(code_ref, str) else ""

    quality: dict[str, Any] = {
        "trust_tier": "BACKGROUND_ONLY",
        "has_embedded_source_code": bool(source_code_text),
        "code_ref": code_ref_text,
    }
    flags: list[str] = []

    if source_code_text:
        quality["embedded_source_sha256"] = sha256_text(source_code_text)
        quality["embedded_source_chars"] = len(source_code_text)

    local_path = resolve_local_ref(code_ref_text) if code_ref_text else None
    if local_path:
        quality["code_ref_path"] = str(local_path)
        quality["code_ref_exists"] = local_path.exists()
        current_hash = sha256_file(local_path) if local_path.exists() and local_path.is_file() else None
        if current_hash:
            quality["code_ref_sha256"] = current_hash
            quality["trust_tier"] = "LIVE_CODE_REF_AVAILABLE"
        elif not local_path.exists():
            flags.append("CODE_REF_MISSING")
    elif code_ref_text:
        flags.append("NON_LOCAL_CODE_REF")
    else:
        flags.append("NO_CODE_REF")

    if source_code_text and quality.get("code_ref_sha256"):
        matches = quality["embedded_source_sha256"] == quality["code_ref_sha256"]
        quality["embedded_source_matches_code_ref"] = matches
        if matches:
            quality["trust_tier"] = "EMBEDDED_SOURCE_MATCHES_LIVE_REF"
        else:
            flags.append("EMBEDDED_SOURCE_DRIFT")

    status = str(payload.get("status", "")).upper()
    if status and status not in {"PASS", "VERIFIED", "CANONICAL"}:
        flags.append(f"PACKET_STATUS_{status}")

    if source_code_text and not quality.get("code_ref_sha256"):
        flags.append("EMBEDDED_SOURCE_WITHOUT_LIVE_REF")

    quality["flags"] = flags
    return {key: value for key, value in quality.items() if value not in ("", [], None)}


def compact_packet_payload(packet_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    trigger = payload.get("trigger") if isinstance(payload.get("trigger"), dict) else {}
    skill = payload.get("skill") if isinstance(payload.get("skill"), dict) else {}
    design = payload.get("design") if isinstance(payload.get("design"), dict) else {}
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    source_quality = inspect_packet_source_quality(payload, skill)

    return {
        "id": packet_id,
        "status": payload.get("status", ""),
        "repo": payload.get("repo", ""),
        "ref": payload.get("ref", ""),
        "source": payload.get("source", ""),
        "notes": payload.get("notes", ""),
        "concepts": trigger.get("concepts", []),
        "language": skill.get("language", ""),
        "input_spec": skill.get("input_spec", ""),
        "output_spec": skill.get("output_spec", ""),
        "code_ref": skill.get("code_ref", ""),
        "source_quality": source_quality,
        "design_patterns": design.get("patterns", []),
        "evidence_count": len(evidence),
    }


def build_rag_provider_configs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return configured RAG providers, preserving legacy single-provider config."""
    configured = config.get("ragProviders")
    providers: list[dict[str, Any]] = []

    if isinstance(configured, list) and configured:
        for item in configured:
            if isinstance(item, dict):
                provider = dict(item)
                if provider.get("enabled", True):
                    providers.append(provider)

    if not providers:
        providers.append({
            "name": "qwen3-deep",
            "role": "deep semantic rerank",
            "enabled": True,
            "always": True,
            "embedUrl": config.get("ragEmbedUrl", DEFAULT_CONFIG["ragEmbedUrl"]),
            "embedModel": config.get("ragEmbedModel", DEFAULT_CONFIG["ragEmbedModel"]),
            "candidateLimit": config.get("ragCandidateLimit", DEFAULT_CONFIG["ragCandidateLimit"]),
            "topK": config.get("ragTopK", DEFAULT_CONFIG["ragTopK"]),
            "contextChars": config.get("ragContextChars", DEFAULT_CONFIG["ragContextChars"]),
            "timeoutSeconds": config.get("ragTimeoutSeconds", DEFAULT_CONFIG["ragTimeoutSeconds"]),
        })

    return providers


def retrieve_rag_context(
    data: dict[str, Any],
    config: dict[str, Any],
    evidence: dict[str, Any],
    pcc_stack: list[dict[str, Any]],
    provider: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Retrieve real background memory from Neural Packets using one embedding provider."""
    if not config.get("ragEnabled", False):
        return {"enabled": False, "packets": []}

    provider_name = str((provider or {}).get("name") or "qwen3-deep")
    embed_url = str(rag_provider_value(
        provider, "embedUrl", config, "ragEmbedUrl", DEFAULT_CONFIG["ragEmbedUrl"]
    ))
    embed_model = str(rag_provider_value(
        provider, "embedModel", config, "ragEmbedModel", DEFAULT_CONFIG["ragEmbedModel"]
    ))

    db_path = resolve_rag_db_path(config)
    if not db_path:
        return {
            "enabled": True,
            "provider": provider_name,
            "packets": [],
            "error": "neural_packets.db not found",
        }

    query = build_rag_query(data, evidence, pcc_stack)
    terms = extract_rag_terms(query)
    candidate_limit = int(rag_provider_value(
        provider, "candidateLimit", config, "ragCandidateLimit", DEFAULT_CONFIG["ragCandidateLimit"]
    ))
    top_k = int(rag_provider_value(
        provider, "topK", config, "ragTopK", DEFAULT_CONFIG["ragTopK"]
    ))
    query_prefix = str(rag_provider_value(
        provider, "queryPrefix", config, "ragQueryPrefix", DEFAULT_CONFIG["ragQueryPrefix"]
    ))

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        candidates = fetch_rag_candidates(conn, terms, candidate_limit)
        if not candidates:
            conn.close()
            return {
                "enabled": True,
                "provider": provider_name,
                "db_path": db_path,
                "embed_url": embed_url,
                "embed_model": embed_model,
                "query_terms": terms,
                "packets": [],
                "error": "no content_index candidates",
            }

        texts = [query_prefix + query] + [candidate["canonical_text"][:900] for candidate in candidates]
        embeddings = rag_embed_texts(texts, config, provider)
        if len(embeddings) == len(texts):
            query_vector = embeddings[0]
            for index, candidate in enumerate(candidates):
                candidate["score"] = cosine_similarity(query_vector, embeddings[index + 1])
                candidate["score_type"] = provider_name
        else:
            for candidate in candidates:
                candidate["score"] = lexical_rag_score(candidate["canonical_text"], terms)
                candidate["score_type"] = "lexical-fallback"

        candidates.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        top = candidates[:max(1, top_k)]
        payloads = fetch_packet_payloads(conn, [item["id"] for item in top])
        conn.close()

        packets: list[dict[str, Any]] = []
        for item in top:
            payload = compact_packet_payload(item["id"], payloads.get(item["id"], {}))
            payload.update({
                "rag_provider": provider_name,
                "score": round(float(item.get("score", 0.0)), 4),
                "score_type": item.get("score_type", ""),
                "status": payload.get("status") or item.get("status", ""),
                "canonical_text": item["canonical_text"][:900],
            })
            packets.append({k: v for k, v in payload.items() if v not in ("", [], None)})

        return {
            "enabled": True,
            "provider": provider_name,
            "db_path": db_path,
            "embed_url": embed_url,
            "embed_model": embed_model,
            "query_terms": terms,
            "candidate_count": len(candidates),
            "packets": packets,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "provider": provider_name,
            "db_path": db_path,
            "embed_url": embed_url,
            "embed_model": embed_model,
            "query_terms": terms,
            "packets": [],
            "error": str(exc),
        }


def retrieve_all_rag_context(
    data: dict[str, Any],
    config: dict[str, Any],
    evidence: dict[str, Any],
    pcc_stack: list[dict[str, Any]],
) -> dict[str, Any]:
    """Retrieve and merge all enabled RAG providers."""
    if not config.get("ragEnabled", False):
        return {"enabled": False, "providers": [], "packets": []}

    provider_contexts = [
        retrieve_rag_context(data, config, evidence, pcc_stack, provider)
        for provider in build_rag_provider_configs(config)
    ]

    merged_packets: dict[str, dict[str, Any]] = {}
    query_terms: list[str] = []
    seen_terms: set[str] = set()
    candidate_count = 0

    for context in provider_contexts:
        candidate_count += int(context.get("candidate_count", 0) or 0)
        for term in context.get("query_terms", []) or []:
            if term not in seen_terms:
                seen_terms.add(term)
                query_terms.append(term)

        for packet in context.get("packets", []) or []:
            if not isinstance(packet, dict) or not packet.get("id"):
                continue
            packet_id = str(packet["id"])
            provider_name = str(packet.get("rag_provider") or context.get("provider") or "unknown")
            if packet_id not in merged_packets:
                merged = dict(packet)
                merged["rag_providers"] = [provider_name]
                merged_packets[packet_id] = merged
            else:
                merged = merged_packets[packet_id]
                providers = merged.setdefault("rag_providers", [])
                if provider_name not in providers:
                    providers.append(provider_name)
                if float(packet.get("score", 0.0)) > float(merged.get("score", 0.0)):
                    merged["score"] = packet.get("score")
                    merged["score_type"] = packet.get("score_type")

    packets = sorted(
        merged_packets.values(),
        key=lambda item: float(item.get("score", 0.0)),
        reverse=True,
    )

    return {
        "enabled": True,
        "db_path": next((ctx.get("db_path") for ctx in provider_contexts if ctx.get("db_path")), None),
        "providers": provider_contexts,
        "query_terms": query_terms,
        "candidate_count": candidate_count,
        "packets": packets,
    }


def format_rag_context(rag_context: dict[str, Any], config: dict[str, Any]) -> str:
    """Render RAG packets as packet-only JSONL-ish context."""
    if not rag_context.get("enabled"):
        return ""

    lines = [
        "--- Real RAG Context (background memory; not verification evidence) ---",
        "packet_policy: RAG packets can guide recall, but live git diff/current file hashes outrank packet source_code.",
        "packet_drift_rule: source_quality.flags containing EMBEDDED_SOURCE_DRIFT means the packet is stale; do not treat it as canonical.",
        f"db_path: {rag_context.get('db_path', '')}",
        f"query_terms: {', '.join(rag_context.get('query_terms', []))}",
        f"candidate_count: {rag_context.get('candidate_count', 0)}",
    ]

    providers = rag_context.get("providers") or []
    if providers:
        provider_summaries = []
        for provider_context in providers:
            provider_summaries.append(
                "{name}@{url} model={model} packets={packets}{error}".format(
                    name=provider_context.get("provider", "unknown"),
                    url=provider_context.get("embed_url", ""),
                    model=provider_context.get("embed_model", ""),
                    packets=len(provider_context.get("packets", []) or []),
                    error=f" error={provider_context['error']}" if provider_context.get("error") else "",
                )
            )
        lines.append("providers: " + " | ".join(provider_summaries))
    else:
        lines.append(f"provider: {rag_context.get('provider', '')}")
        lines.append(f"embed_url: {rag_context.get('embed_url', '')}")
        lines.append(f"embed_model: {rag_context.get('embed_model', '')}")

    if rag_context.get("error"):
        lines.append(f"rag_error: {rag_context['error']}")

    packets = rag_context.get("packets") or []
    if packets:
        lines.append("packets:")
        for packet in packets:
            lines.append(json.dumps(packet, ensure_ascii=False))
    else:
        lines.append("packets: []")

    rendered = "\n".join(lines)
    return rendered[: int(config.get("ragContextChars", DEFAULT_CONFIG["ragContextChars"]))]


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
    "diffPatchChars": 12000,
    "passthroughEvidenceChars": 6000,
    "styleNote": "Return critique in Japanese. Be dense, concrete, and merciless about missing evidence.",
    "focusAreas": [
        "completion illusion detection",
        "missing verification evidence",
        "git diff vs claimed changes mismatch",
        "test/lint exit code verification",
        "scope violation detection",
    ],
    # ACP x PCC context stack
    "includeRootL0": True,
    "maxPccLayers": 6,
    "pccStack": DEFAULT_PCC_STACK,
    # Real RAG: BGE-M3-lite (:8094) is always-on; Qwen3 (:8093) is deeper.
    "ragEnabled": True,
    "ragDbPath": DEFAULT_RAG_DB_CANDIDATES[0],
    "ragEmbedUrl": "http://127.0.0.1:8093/v1/embeddings",
    "ragEmbedModel": "Qwen/Qwen3-Embedding-8B",
    "ragQueryPrefix": "Instruct: Find relevant technical documentation\nQuery: ",
    "ragCandidateLimit": 80,
    "ragTopK": 5,
    "ragContextChars": 5000,
    "ragTimeoutSeconds": 20,
    "ragProviders": [
        {
            "name": "bge-m3-lite",
            "role": "lightweight always-on background memory",
            "enabled": True,
            "always": True,
            "embedUrl": "http://127.0.0.1:8094/v1/embeddings",
            "embedModel": "BAAI/bge-m3",
            "candidateLimit": 50,
            "topK": 3,
            "contextChars": 1800,
            "timeoutSeconds": 8,
        },
        {
            "name": "qwen3-deep",
            "role": "deep semantic packet rerank",
            "enabled": True,
            "always": True,
            "embedUrl": "http://127.0.0.1:8093/v1/embeddings",
            "embedModel": "Qwen/Qwen3-Embedding-8B",
            "candidateLimit": 80,
            "topK": 5,
            "contextChars": 5000,
            "timeoutSeconds": 20,
        },
    ],
    # FT data collection
    "ft_data_dir": os.path.expanduser("~/.vortex-critic"),
    "ft_collect": True,
    "sourceProvenance": True,
    "strictMissingVerification": True,
}


def load_config() -> dict[str, Any]:
    config_path = Path(__file__).with_name("vortex-critic.config.json")
    if not config_path.exists():
        config_path = Path(__file__).with_name("deepseek-critic.config.json")
    if not config_path.exists():
        return normalize_config_paths(dict(DEFAULT_CONFIG))

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_CONFIG)
        merged.update(data)
        return normalize_config_paths(merged)
    except Exception:
        print("VORTEX critic config JSON is invalid.", file=sys.stderr)
        return normalize_config_paths(dict(DEFAULT_CONFIG))


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
    pcc_stack: list[dict[str, Any]],
    rag_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build DeepSeek messages with ACP x PCC stack injection."""
    stack_block = format_pcc_stack(pcc_stack)
    stack_labels = ", ".join(
        f"{layer['name']}={layer['coordinate']}" for layer in pcc_stack
    )
    style_note = config.get("styleNote", DEFAULT_CONFIG["styleNote"])
    focus_areas = "\n".join(f"- {item}" for item in config.get("focusAreas", []))

    prompt = str(data.get("prompt", ""))
    active_file = data.get("activeFile")
    workspace_root = data.get("workspaceRoot")
    focus_block = f"Focus Areas:\n{focus_areas}\n" if focus_areas else ""

    # ── System Prompt: PCC + VORTEX ──
    system_prompt = (
        f"{format_coordinate_header(coordinate, preset_name)}\n\n"
        f"{stack_block}\n\n"
        "[VORTEX Evidence Verification Protocol]\n"
        "You are a read-only critic and evidence auditor for another coding agent.\n"
        "You do not implement the task. You verify claims against objective evidence.\n\n"
        "Core Principle:\n"
        "- workerの自己申告は信用しない\n"
        "- git diff、tests、lint、exit code、changed files で判定する\n"
        "- スコープ外変更は落とす\n\n"
        "Stack Semantics:\n"
        "- Root L0 is the invariant baseline, not a normal preset.\n"
        "- Explorer finds assumptions and alternatives.\n"
        "- Auditor judges evidence only.\n"
        "- Reviewer checks implementation risk and blast radius.\n"
        "- Synthesizer emits the final verdict.\n"
        "- Real RAG packets are background memory only; they do not count as proof of completion.\n"
        f"- Active stack: {stack_labels}\n"
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
        '  "quality_flags": ["NO_GIT_PROVENANCE | CHANGED_FILES_WITHOUT_VERIFICATION | DIFF_PATCH_TRUNCATED | ..."],\n'
        '  "source_provenance": {"critic_source_tier": "GIT_TRACKED_CLEAN | LOCAL_WORKTREE_DIRTY | FILE_ONLY_NO_GIT_PROVENANCE"},\n'
        '  "pcc_stack_used": ["root-l0:999299119", "lane:coordinate"],\n'
        '  "rag_packets_used": ["packet-id", "..."],\n'
        '  "lane_findings": {"explorer": [], "auditor": [], "reviewer": []},\n'
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

    source_provenance = evidence.get("critic_source")
    if isinstance(source_provenance, dict):
        compact_source = {
            "version": source_provenance.get("version"),
            "path": source_provenance.get("path"),
            "sha256": source_provenance.get("sha256"),
            "source_tier": source_provenance.get("source_tier"),
            "repo_root": source_provenance.get("repo_root"),
            "repo_relpath": source_provenance.get("repo_relpath"),
            "head_sha": source_provenance.get("head_sha"),
            "branch": source_provenance.get("branch"),
            "git_status": source_provenance.get("git_status"),
            "remote_origin": source_provenance.get("remote_origin"),
            "trust_policy": source_provenance.get("trust_policy"),
        }
        context_lines.append("\n--- Critic Source Provenance (live script, not packet memory) ---")
        context_lines.append(json.dumps(
            {k: v for k, v in compact_source.items() if v not in ("", [], None)},
            ensure_ascii=False,
        ))

    quality = evidence.get("evidence_quality")
    if isinstance(quality, dict):
        context_lines.append("\n--- Evidence Quality Summary ---")
        context_lines.append(json.dumps(quality, ensure_ascii=False))

    task_evidence_present = any(key in evidence for key in TASK_EVIDENCE_KEYS)
    if task_evidence_present:
        context_lines.append("\n--- Objective Evidence (collected by VORTEX, NOT self-reported) ---")
        if "repo_root" in evidence:
            context_lines.append(f"Repo root: {evidence['repo_root']}")
        if "head_sha" in evidence:
            context_lines.append(f"HEAD SHA: {evidence['head_sha']}")
        if "branch" in evidence:
            context_lines.append(f"Branch: {evidence['branch']}")
        if "status_short" in evidence:
            context_lines.append(f"Git status --short:\n{evidence['status_short']}")
        if "diff_stat" in evidence:
            context_lines.append(f"Git diff stat:\n{evidence['diff_stat']}")
        if "changed_files" in evidence:
            context_lines.append(f"Changed files: {', '.join(evidence['changed_files'])}")
        if "staged_files" in evidence:
            context_lines.append(f"Staged files: {', '.join(evidence['staged_files'])}")
        if "untracked_files" in evidence:
            context_lines.append(f"Untracked files: {', '.join(evidence['untracked_files'])}")
        if "untracked_patch_skipped" in evidence:
            context_lines.append(
                "Untracked patch skipped: "
                + json.dumps(evidence["untracked_patch_skipped"], ensure_ascii=False)
            )
        if "last_commit" in evidence:
            context_lines.append(f"Last commit: {evidence['last_commit']}")
        if "diff_patch" in evidence:
            if evidence.get("diff_patch_truncated"):
                context_lines.append(
                    f"Diff patch truncated: true; full_chars={evidence.get('diff_patch_full_chars')}"
                )
            context_lines.append(f"Diff patch (truncated):\n```diff\n{evidence['diff_patch']}\n```")
        if "test_artifacts_found" in evidence:
            context_lines.append(f"Test artifacts found: {', '.join(evidence['test_artifacts_found'])}")
        if "test_artifacts_detail" in evidence:
            context_lines.append(
                "Test artifact details: "
                + json.dumps(evidence["test_artifacts_detail"], ensure_ascii=False)
            )
        for key in (
            "test_command",
            "lint_command",
            "typecheck_command",
            "security_command",
            "verification_command",
        ):
            if key in evidence:
                context_lines.append(f"{key}: {evidence[key]}")
        for key in sorted(EXIT_CODE_KEYS):
            if key in evidence:
                context_lines.append(f"{key}: {evidence[key]}")
        for key in sorted(TEXT_EVIDENCE_KEYS):
            if key in evidence:
                context_lines.append(f"{key}:\n```\n{evidence[key]}\n```")
        if "git_errors" in evidence:
            context_lines.append("Git collection errors: " + "; ".join(evidence["git_errors"]))
    else:
        context_lines.append("NO TASK EVIDENCE FOUND. Worker has not run or supplied diff/test/lint verification.")

    if isinstance(quality, dict) and quality.get("missing"):
        context_lines.append(
            "Missing verification warning: "
            + "; ".join(str(item) for item in quality.get("missing", []))
        )

    if rag_context:
        rag_block = format_rag_context(rag_context, config)
        if rag_block:
            context_lines.append("\n" + rag_block)

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
    pcc_stack: list[dict[str, Any]],
    rag_context: dict[str, Any],
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
        "pcc_stack": [
            {
                "name": layer["name"],
                "role": layer["role"],
                "preset": layer["preset"],
                "coordinate": layer["coordinate"],
            }
            for layer in pcc_stack
        ],
        "model": model,
        "usage": usage,
        "critic_source": evidence.get("critic_source"),
        "evidence_quality": evidence.get("evidence_quality"),
        "rag": {
            "enabled": bool(rag_context.get("enabled")),
            "db_path": rag_context.get("db_path"),
            "providers": [
                {
                    "name": provider.get("provider"),
                    "embed_url": provider.get("embed_url"),
                    "embed_model": provider.get("embed_model"),
                    "candidate_count": provider.get("candidate_count", 0),
                    "packet_ids": [
                        packet.get("id") for packet in provider.get("packets", [])
                        if isinstance(packet, dict) and packet.get("id")
                    ],
                    "error": provider.get("error"),
                }
                for provider in rag_context.get("providers", [])
                if isinstance(provider, dict)
            ],
            "query_terms": rag_context.get("query_terms", []),
            "candidate_count": rag_context.get("candidate_count", 0),
            "packet_ids": [
                packet.get("id") for packet in rag_context.get("packets", [])
                if isinstance(packet, dict) and packet.get("id")
            ],
            "error": rag_context.get("error"),
        },

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
            "has_exit_codes": any(key in evidence for key in EXIT_CODE_KEYS),
            "quality_level": (
                evidence.get("evidence_quality", {}).get("level")
                if isinstance(evidence.get("evidence_quality"), dict)
                else None
            ),
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

    dry_run = bool(data.get("dry_run")) or os.environ.get("VORTEX_CRITIC_DRY_RUN") == "1"

    # ── Resolve PCC Coordinate ──
    preset_name = config.get("preset", "渦")
    coordinate = resolve_preset(preset_name)

    # Allow runtime override via stdin data
    if "pcc_coordinate" in data:
        coordinate = str(data["pcc_coordinate"])
        preset_name = "custom"
    if "pcc_overrides" in data:
        coordinate = apply_overrides(coordinate, str(data["pcc_overrides"]))

    pcc_stack = build_pcc_stack(data, config, coordinate, preset_name)
    stack_label = " > ".join(
        f"{layer['name']}:{layer['coordinate']}" for layer in pcc_stack
    )

    print(f"[VORTEX] PCC coordinate: {coordinate} (preset: {preset_name})", file=sys.stderr)
    print(f"[VORTEX] Constraints: {generate_constraints(coordinate)}", file=sys.stderr)
    print(f"[VORTEX] PCC stack: {stack_label}", file=sys.stderr)

    # ── Collect Objective Evidence ──
    workspace_root = data.get("workspaceRoot", "")
    evidence: dict[str, Any] = {}
    if config.get("sourceProvenance", True):
        evidence["critic_source"] = collect_script_provenance()
    if workspace_root and Path(workspace_root).is_dir():
        evidence.update(collect_git_evidence(
            workspace_root,
            int(config.get("diffPatchChars", DEFAULT_CONFIG["diffPatchChars"])),
        ))
        evidence.update(collect_test_evidence(workspace_root))

    attach_passthrough_evidence(
        data,
        evidence,
        int(config.get("passthroughEvidenceChars", DEFAULT_CONFIG["passthroughEvidenceChars"])),
    )
    evidence["evidence_quality"] = summarize_evidence_quality(evidence)

    # ── Real RAG Background Memory ──
    if dry_run and not data.get("dry_run_include_rag"):
        rag_context = {
            "enabled": False,
            "providers": [],
            "packets": [],
            "dry_run_skipped": True,
        }
    else:
        rag_context = retrieve_all_rag_context(data, config, evidence, pcc_stack)
    rag_packets = rag_context.get("packets", [])
    if rag_context.get("enabled"):
        provider_labels = []
        for provider_context in rag_context.get("providers", []) or []:
            provider_labels.append(
                f"{provider_context.get('provider', 'unknown')}:{len(provider_context.get('packets', []) or [])}"
            )
            if provider_context.get("error"):
                print(
                    f"[VORTEX] RAG warning ({provider_context.get('provider', 'unknown')}): "
                    f"{provider_context['error']}",
                    file=sys.stderr,
                )
        provider_label = ",".join(provider_labels) if provider_labels else "none"
        print(
            "[VORTEX] RAG: "
            f"{len(rag_packets)} merged packets from {rag_context.get('db_path', 'unknown')} "
            f"providers={provider_label}",
            file=sys.stderr,
        )
        if rag_context.get("error"):
            print(f"[VORTEX] RAG warning: {rag_context['error']}", file=sys.stderr)

    # ── Build + Call ──
    messages = build_messages(
        data, config, evidence, coordinate, preset_name, pcc_stack, rag_context
    )

    if dry_run:
        dry_payload = {
            "script_version": SCRIPT_VERSION,
            "coordinate": coordinate,
            "preset": preset_name,
            "pcc_stack": stack_label,
            "critic_source": evidence.get("critic_source"),
            "evidence_quality": evidence.get("evidence_quality"),
            "rag": {
                "enabled": rag_context.get("enabled"),
                "dry_run_skipped": rag_context.get("dry_run_skipped", False),
                "packet_count": len(rag_packets),
                "providers": [
                    {
                        "provider": provider.get("provider"),
                        "packets": len(provider.get("packets", []) or []),
                        "error": provider.get("error"),
                    }
                    for provider in rag_context.get("providers", []) or []
                    if isinstance(provider, dict)
                ],
            },
            "message_chars": {
                "system": len(messages[0]["content"]),
                "user": len(messages[1]["content"]),
            },
            "user_prompt_preview": messages[1]["content"][:2400],
        }
        return emit(
            "[VORTEX Critic Dry Run | no DeepSeek call]\n"
            + json.dumps(dry_payload, ensure_ascii=False, indent=2)
        )

    api_key = resolve_api_key(config)
    if not api_key:
        print("DeepSeek API key missing.", file=sys.stderr)
        return emit(None)

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
        pcc_stack=pcc_stack,
        rag_context=rag_context,
        evidence=evidence,
        messages=messages,
        response_text=response_text,
        usage=result.get("usage", {}),
        model=result.get("model", ""),
    )

    # ── Emit to Copilot Hook ──
    rag_provider_tag = ",".join(
        f"{provider.get('provider', 'unknown')}:{len(provider.get('packets', []) or [])}"
        for provider in rag_context.get("providers", []) or []
        if isinstance(provider, dict)
    ) or "none"
    additional_context = (
        f"[VORTEX Critic | PCC Stack:{stack_label} | RAG:{len(rag_packets)}:{rag_provider_tag}]\n"
        f"{response_text.strip()}"
    )
    return emit(additional_context)


if __name__ == "__main__":
    raise SystemExit(main())
