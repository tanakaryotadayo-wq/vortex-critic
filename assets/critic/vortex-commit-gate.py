#!/usr/bin/env python3
"""
vortex-commit-gate.py - Perfect Equilibrium commit gate.

The gate combines deterministic local evidence, Gemini CLI, GitHub Copilot CLI,
and a local port of the Perfect Balance/Perfect Equilibrium formula. A commit is
allowed only when objective verification and both live critic lanes pass.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "1.1.0-perfect-equilibrium-commit-gate"
SCRIPT_DIR = Path(__file__).resolve().parent
CORE_SCRIPT = SCRIPT_DIR / "vortex-critic.py"
GEMINI_SCRIPT = SCRIPT_DIR / "gemini-cli-critic.py"
COPILOT_SCRIPT = SCRIPT_DIR / "copilot-cli-critic.py"
REPORT_DIR = Path.home() / ".vortex-critic" / "commit-gates"
PE_STATE_PATH = REPORT_DIR / "pe_state.json"


def load_core() -> Any:
    spec = importlib.util.spec_from_file_location("vortex_critic_core", CORE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load VORTEX critic core: {CORE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load_core()


PE_MODEL_PRESETS: dict[str, dict[str, float | str]] = {
    "claude_opus": {"name": "Claude Opus", "error_rate": 0.02, "correction_power": 0.80},
    "claude_sonnet": {"name": "Claude Sonnet", "error_rate": 0.04, "correction_power": 0.75},
    "gemini_pro": {"name": "Gemini Pro", "error_rate": 0.12, "correction_power": 0.65},
    "gemini_flash": {"name": "Gemini Flash", "error_rate": 0.18, "correction_power": 0.55},
    "qwen3_coder": {"name": "Qwen3 Coder", "error_rate": 0.06, "correction_power": 0.70},
    "qwen35_9b": {"name": "Qwen3.5 9B", "error_rate": 0.10, "correction_power": 0.60},
    "commit_gate": {"name": "VORTEX Commit Gate", "error_rate": 0.04, "correction_power": 0.82},
}


class PerfectEquilibriumEngine:
    """Python port of the local Perfect Balance engine formula."""

    def __init__(self, preset: str = "commit_gate", context_decay: float = 0.0) -> None:
        model = PE_MODEL_PRESETS.get(preset, PE_MODEL_PRESETS["commit_gate"])
        self.model_name = str(model["name"])
        self.e_base = float(model["error_rate"])
        self.c_psi_base = float(model["correction_power"])
        self.context_decay = context_decay
        self.p_hall = 0.0
        self.step_count = 0
        self.weighted_steps = 0
        self.consecutive_fails = 0
        self.total_fails = 0
        self.total_steps = 0
        self.karma = 1.0
        self.sabotage_events = 0
        self.history: list[dict[str, Any]] = []

    def current_e(self) -> float:
        return min(1.0, self.e_base + self.context_decay * self.weighted_steps)

    def effective_cpsi(self, complexity: float, layer_scores: dict[str, float] | None = None) -> float:
        base_cpsi = self.c_psi_base
        if layer_scores:
            l0 = clamp01(layer_scores.get("l0", 0.0))
            l1 = clamp01(layer_scores.get("l1", 0.0))
            l2 = clamp01(layer_scores.get("l2", 0.0))
            l3 = clamp01(layer_scores.get("l3", 0.0))
            l4 = clamp01(layer_scores.get("l4", 0.0))
            base_cpsi = 1 - ((1 - l0) * (1 - l1) * (1 - l2) * (1 - l3) * (1 - l4))
        degradation = 1 / (1 + clamp(complexity, 0.0, 10.0) * 0.1)
        return clamp01(base_cpsi * degradation * self.karma)

    @staticmethod
    def compute_limit(e_current: float, c_psi_eff: float) -> float:
        if c_psi_eff >= 1 or e_current <= 0:
            return 0.0
        alpha = 1 - c_psi_eff
        return (alpha * e_current) / (1 - alpha * (1 - e_current))

    def record_step(
        self,
        audit_result: str | None,
        weight: int,
        complexity: float,
        layer_scores: dict[str, float] | None = None,
        label: str = "",
    ) -> dict[str, Any]:
        weight = max(1, min(20, round(weight)))
        complexity = clamp(complexity, 0.0, 10.0)

        for _ in range(weight):
            self.step_count += 1
            self.weighted_steps += 1
            e_current = self.current_e()
            self.p_hall = self.p_hall + (1 - self.p_hall) * e_current

        c_psi_eff = self.effective_cpsi(complexity, layer_scores)
        self.p_hall = (1 - c_psi_eff) * self.p_hall

        self.total_steps += 1
        if audit_result == "FAIL":
            self.consecutive_fails += 1
            self.total_fails += 1
            self.p_hall = min(1.0, self.p_hall * 1.5)
        else:
            self.consecutive_fails = 0

        e_current = self.current_e()
        p_limit = self.compute_limit(e_current, c_psi_eff)
        fail_rate = self.total_fails / self.total_steps if self.total_steps else 0
        if self.consecutive_fails >= 3 or (self.total_steps >= 5 and fail_rate > 0.5):
            status = "SABOTAGE_DETECTED"
            self.sabotage_events += 1
            self.karma = max(0.1, self.karma * 0.7)
        elif p_limit >= 1.0 or e_current >= 1.0:
            status = "COLLAPSED"
        elif abs(p_limit - self.p_hall) < 0.0001 or self.p_hall >= p_limit * 0.95:
            status = "STABLE"
        else:
            status = "EVOLVING"

        record = {
            "label": label,
            "step": self.step_count,
            "p_hall": round(self.p_hall, 4),
            "p_limit": round(p_limit, 4),
            "e_current": round(e_current, 4),
            "c_psi_effective": round(c_psi_eff, 4),
            "weight": weight,
            "complexity": round(complexity, 2),
            "status": status,
            "audit_result": audit_result,
            "layer_scores": layer_scores or {},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.history.append(record)
        return record

    def save_state(self, path: Path) -> None:
        """Persist PE state to disk for cross-session sabotage detection."""
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "model_name": self.model_name,
            "e_base": self.e_base,
            "c_psi_base": self.c_psi_base,
            "p_hall": self.p_hall,
            "step_count": self.step_count,
            "weighted_steps": self.weighted_steps,
            "consecutive_fails": self.consecutive_fails,
            "total_fails": self.total_fails,
            "total_steps": self.total_steps,
            "karma": self.karma,
            "sabotage_events": self.sabotage_events,
            "history": self.history[-20:],
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load_state(cls, path: Path, preset: str = "commit_gate") -> "PerfectEquilibriumEngine":
        """Restore PE state from disk. Falls back to fresh engine if file missing/corrupt."""
        engine = cls(preset)
        if not path.exists():
            return engine
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            engine.p_hall = float(state.get("p_hall", 0.0))
            engine.step_count = int(state.get("step_count", 0))
            engine.weighted_steps = int(state.get("weighted_steps", 0))
            engine.consecutive_fails = int(state.get("consecutive_fails", 0))
            engine.total_fails = int(state.get("total_fails", 0))
            engine.total_steps = int(state.get("total_steps", 0))
            engine.karma = float(state.get("karma", 1.0))
            engine.sabotage_events = int(state.get("sabotage_events", 0))
            engine.history = list(state.get("history", []))[-20:]
        except Exception:
            pass
        return engine

    def status(self) -> dict[str, Any]:
        e_current = self.current_e()
        c_psi_eff = self.effective_cpsi(0)
        p_limit = self.compute_limit(e_current, c_psi_eff)
        fail_rate = self.total_fails / self.total_steps if self.total_steps else 0
        if self.consecutive_fails >= 3 or (self.total_steps >= 5 and fail_rate > 0.5):
            status = "SABOTAGE_DETECTED"
        elif p_limit >= 1.0 or e_current >= 1.0:
            status = "COLLAPSED"
        elif self.step_count == 0:
            status = "IDLE"
        elif abs(p_limit - self.p_hall) < 0.0001 or self.p_hall >= p_limit * 0.95:
            status = "STABLE"
        else:
            status = "EVOLVING"
        return {
            "model": self.model_name,
            "step_count": self.step_count,
            "weighted_steps": self.weighted_steps,
            "p_hall": round(self.p_hall, 4),
            "p_limit": round(p_limit, 4),
            "e_base": self.e_base,
            "e_current": round(e_current, 4),
            "c_psi_base": self.c_psi_base,
            "c_psi_effective": round(c_psi_eff, 4),
            "status": status,
            "karma": round(self.karma, 4),
            "consecutive_fails": self.consecutive_fails,
            "sabotage_events": self.sabotage_events,
            "history": self.history[-10:],
        }


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def clamp01(value: float) -> float:
    return clamp(value, 0.0, 1.0)


def read_stdin_json() -> dict[str, Any]:
    if sys.stdin.isatty():
        return {}
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"prompt": raw}
    except Exception:
        return {"prompt": raw}


def run_shell_command(command: str, cwd: str, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["/bin/zsh", "-lc", command],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
        return {
            "command": command,
            "exit_code": proc.returncode,
            "elapsed": round(time.monotonic() - started, 1),
            "output": output[-12000:],
        }
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")).strip()
        return {
            "command": command,
            "exit_code": -1,
            "elapsed": timeout,
            "output": output[-12000:],
            "error": "TIMEOUT",
        }


def run_git(args: list[str], cwd: str, timeout: int = 30) -> dict[str, Any]:
    started = time.monotonic()
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "args": args,
        "exit_code": proc.returncode,
        "elapsed": round(time.monotonic() - started, 1),
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def hook_context(stdout: str) -> str:
    try:
        payload = json.loads(stdout)
        return str(payload.get("hookSpecificOutput", {}).get("additionalContext", ""))
    except Exception:
        return ""


def run_lane(name: str, script: Path, packet: dict[str, Any], timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["python3", str(script)],
            input=json.dumps(packet, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        context = hook_context(stdout)
        return {
            "name": name,
            "script": str(script),
            "exit_code": proc.returncode,
            "elapsed": round(time.monotonic() - started, 1),
            "stdout_chars": len(stdout),
            "stderr": stderr[-4000:],
            "additional_context": context,
            "additional_context_chars": len(context),
            "status": "RESPONDED" if context else "NO_CONTEXT",
        }
    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "script": str(script),
            "exit_code": -1,
            "elapsed": timeout,
            "stderr": "TIMEOUT",
            "additional_context": "",
            "additional_context_chars": 0,
            "status": "TIMEOUT",
        }


def extract_first_json(text: str) -> dict[str, Any] | None:
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    for block in blocks:
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                return data
        except Exception:
            continue

    start_positions = [match.start() for match in re.finditer(r"\{", text)]
    for start in start_positions:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:index + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict):
                            return data
                    except Exception:
                        break
    return None


def classify_lane(lane: dict[str, Any], live: bool) -> dict[str, Any]:
    text = str(lane.get("additional_context") or "")
    lowered = text.lower()
    structured = extract_first_json(text)
    verdict = ""
    confidence = None
    if structured:
        verdict = str(structured.get("verdict", "")).upper()
        confidence = structured.get("confidence")

    if not live and "dry run" in lowered:
        passed = True
        lane_verdict = "DRY_RUN"
    elif lane.get("status") != "RESPONDED":
        passed = False
        lane_verdict = str(lane.get("status") or "NO_CONTEXT")
    elif verdict == "VERIFIED":
        passed = True
        lane_verdict = verdict
    elif verdict in {"UNVERIFIED", "UNVERIFIED_MUTATION"}:
        passed = False
        lane_verdict = verdict
    elif "verdict: fail" in lowered or '"verdict": "fail"' in lowered:
        passed = False
        lane_verdict = "FAIL_TEXT"
    elif "verdict: pass" in lowered or '"verdict": "pass"' in lowered:
        passed = True
        lane_verdict = "PASS_TEXT"
    else:
        passed = False
        lane_verdict = "UNPARSEABLE"

    return {
        "name": lane.get("name"),
        "status": lane.get("status"),
        "exit_code": lane.get("exit_code"),
        "elapsed": lane.get("elapsed"),
        "chars": lane.get("additional_context_chars", 0),
        "verdict": lane_verdict,
        "passed": passed,
        "confidence": confidence,
        "structured": structured,
        "mentions_diff": "diff" in lowered,
        "mentions_evidence_quality": "evidence_quality" in lowered or "evidence quality" in lowered,
        "mentions_source_provenance": "source_provenance" in lowered or "source provenance" in lowered,
    }


def status_paths(status_short: str) -> list[str]:
    paths: list[str] = []
    for line in status_short.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip())
    return paths


def scope_result(changed_files: list[str], patterns: list[str]) -> dict[str, Any]:
    if not patterns:
        return {"ok": True, "patterns": [], "violations": []}
    violations = [
        path for path in changed_files
        if not any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
    ]
    return {"ok": not violations, "patterns": patterns, "violations": violations}


def estimate_complexity(evidence: dict[str, Any], test_count: int) -> float:
    changed_count = len(evidence.get("changed_files", []) or [])
    patch_chars = int(evidence.get("diff_patch_full_chars") or len(str(evidence.get("diff_patch") or "")))
    status_count = len(status_paths(str(evidence.get("status_short") or "")))
    return clamp((changed_count * 0.45) + (status_count * 0.15) + (patch_chars / 4500) + (test_count * 0.4), 0.0, 10.0)


def estimate_weight(evidence: dict[str, Any]) -> int:
    changed_count = len(evidence.get("changed_files", []) or [])
    patch_chars = int(evidence.get("diff_patch_full_chars") or len(str(evidence.get("diff_patch") or "")))
    return max(1, min(20, 1 + math.ceil(patch_chars / 5000) + changed_count // 6))


def make_layer_scores(
    evidence: dict[str, Any],
    tests_ok: bool,
    has_test_command: bool,
    live: bool,
    lane_summaries: list[dict[str, Any]],
    scope_ok: bool,
) -> dict[str, float]:
    quality = evidence.get("evidence_quality") if isinstance(evidence.get("evidence_quality"), dict) else {}
    has_git = bool(quality.get("has_git_state"))
    has_diff = bool(quality.get("has_diff"))
    changed_count = int(quality.get("changed_file_count") or 0)
    lane_score = 0.0
    if live and lane_summaries:
        lane_score = sum(1 for lane in lane_summaries if lane.get("passed")) / len(lane_summaries)
    elif not live:
        lane_score = 0.35

    return {
        "l0": 1.0 if has_git else 0.2,
        "l1": 1.0 if changed_count and has_diff else 0.55 if not changed_count else 0.2,
        "l2": 1.0 if tests_ok and has_test_command else 0.0,
        "l3": lane_score,
        "l4": 1.0 if scope_ok and not quality.get("failing_exit_codes") else 0.1,
    }


def notify_failure(title: str, message: str) -> None:
    def clean(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")[:180]

    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{clean(message)}" with title "{clean(title)}"',
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        pass


def build_packet(args: argparse.Namespace, evidence: dict[str, Any], test_results: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = args.prompt or (
        "VORTEX commit gate audit. Decide if the current git changes are safe to commit. "
        "Return VERIFIED only when objective diff, scope, and verification command evidence prove the change."
    )
    packet: dict[str, Any] = {
        "prompt": prompt,
        "workspaceRoot": args.workspace_root,
        "dry_run": not args.live,
    }
    if args.include_rag:
        packet["dry_run_include_rag"] = True
    if args.scope_file:
        packet["scope_files"] = args.scope_file
    if test_results:
        commands = [str(item.get("command", "")) for item in test_results if item.get("command")]
        outputs = []
        for item in test_results:
            outputs.append(
                f"$ {item.get('command')}\nexit={item.get('exit_code')} elapsed={item.get('elapsed')}s\n{item.get('output', '')}"
            )
        packet["test_command"] = "\n".join(commands)
        packet["test_exit_code"] = 0 if all(item.get("exit_code") == 0 for item in test_results) else 1
        packet["test_output"] = "\n\n".join(outputs)[-12000:]

    if evidence.get("untracked_patch_skipped"):
        packet["verification_log"] = (
            "Untracked patch skipped: "
            + json.dumps(evidence["untracked_patch_skipped"], ensure_ascii=False)
        )
    return packet


def write_report(report: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"gate-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.json"
    report["report_path"] = str(path)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run VORTEX Perfect Equilibrium commit gate.")
    parser.add_argument("--workspace-root", default=os.getcwd(), help="Git workspace root. Default: cwd.")
    parser.add_argument("--prompt", default="", help="Task or commit intent for the critic lanes.")
    parser.add_argument("--scope-file", action="append", default=[], help="Allowed file glob. Repeatable.")
    parser.add_argument("--test-command", action="append", default=[], help="Verification command to run before critic lanes. Repeatable.")
    parser.add_argument("--test-timeout", type=int, default=240, help="Timeout seconds for each test command.")
    parser.add_argument("--lane-timeout", type=int, default=300, help="Timeout seconds for each AI critic lane.")
    parser.add_argument("--live", action="store_true", help="Call Gemini CLI and Copilot CLI. Default only dry-runs prompt/evidence.")
    parser.add_argument("--require-live", action="store_true", help="Fail instead of producing a non-committable dry-run verdict.")
    parser.add_argument("--include-rag", action="store_true", help="Allow RAG retrieval in dry-run lane prompts.")
    parser.add_argument("--allow-no-test", action="store_true", help="Allow gate without an explicit verification command.")
    parser.add_argument("--max-p-hall", type=float, default=0.20, help="Maximum allowed PE hallucination probability.")
    parser.add_argument("--commit-message", default="", help="If gate passes, run git commit -m with this message.")
    parser.add_argument("--stage-all", action="store_true", help="Run git add -A before committing after the gate passes.")
    parser.add_argument("--allow-dry-run-commit", action="store_true", help="Dangerous: allow commit after dry-run lanes.")
    parser.add_argument("--no-notify", action="store_true", help="Disable macOS failure notification.")
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    args = parser.parse_args()

    stdin_packet = read_stdin_json()
    if stdin_packet.get("prompt") and not args.prompt:
        args.prompt = str(stdin_packet["prompt"])

    workspace = str(Path(args.workspace_root).expanduser().resolve())
    if not Path(workspace).is_dir():
        print(f"workspace root does not exist: {workspace}", file=sys.stderr)
        return 2
    args.workspace_root = workspace

    test_results = [
        run_shell_command(command, workspace, args.test_timeout)
        for command in args.test_command
    ]
    tests_ok = bool(test_results) and all(item.get("exit_code") == 0 for item in test_results)
    has_test_command = bool(test_results)

    evidence = core.collect_git_evidence(workspace, int(core.DEFAULT_CONFIG["diffPatchChars"]))
    evidence.update(core.collect_test_evidence(workspace))
    core.attach_passthrough_evidence(
        build_packet(args, evidence, test_results),
        evidence,
        int(core.DEFAULT_CONFIG["passthroughEvidenceChars"]),
    )
    evidence["evidence_quality"] = core.summarize_evidence_quality(evidence)

    changed_files = list(evidence.get("changed_files", []) or [])
    status_file_list = status_paths(str(evidence.get("status_short") or ""))
    if status_file_list:
        changed_files = list(dict.fromkeys(changed_files + status_file_list))
    scope = scope_result(changed_files, args.scope_file)

    packet = build_packet(args, evidence, test_results)
    # live/dry_run の分岐は各レーンスクリプト内部で行う
    # (packet["dry_run"] が True なら各スクリプトは API を呼ばずプレビューを返す)
    lanes: dict[str, Any] = {}
    lanes["gemini-cli"] = run_lane("gemini-cli", GEMINI_SCRIPT, packet, args.lane_timeout)
    lanes["copilot-cli"] = run_lane("copilot-cli", COPILOT_SCRIPT, packet, args.lane_timeout)

    lane_summaries = [classify_lane(lane, args.live) for lane in lanes.values()]
    lane_pass = all(item.get("passed") for item in lane_summaries)

    complexity = estimate_complexity(evidence, len(test_results))
    weight = estimate_weight(evidence)
    layer_scores = make_layer_scores(
        evidence=evidence,
        tests_ok=tests_ok,
        has_test_command=has_test_command,
        live=args.live,
        lane_summaries=lane_summaries,
        scope_ok=bool(scope["ok"]),
    )
    pe = PerfectEquilibriumEngine.load_state(PE_STATE_PATH, "commit_gate")
    pe.record_step(
        "PASS" if tests_ok or args.allow_no_test else "FAIL",
        weight=weight,
        complexity=complexity,
        layer_scores=layer_scores,
        label="deterministic evidence",
    )
    for lane in lane_summaries:
        pe.record_step(
            "PASS" if lane.get("passed") else "FAIL",
            weight=max(1, weight // 2),
            complexity=complexity,
            layer_scores=layer_scores,
            label=str(lane.get("name")),
        )
    pe_status = pe.status()
    pe.save_state(PE_STATE_PATH)

    reasons: list[str] = []
    quality = evidence.get("evidence_quality") if isinstance(evidence.get("evidence_quality"), dict) else {}
    if not changed_files:
        reasons.append("NO_WORKSPACE_MUTATION_DETECTED")
    if not args.allow_no_test and not has_test_command:
        reasons.append("NO_EXPLICIT_TEST_COMMAND")
    if has_test_command and not tests_ok:
        reasons.append("TEST_COMMAND_FAILED")
    if not scope["ok"]:
        reasons.append("SCOPE_VIOLATION")
    if quality.get("failing_exit_codes"):
        reasons.append("FAILING_EXIT_CODE")
    if evidence.get("untracked_patch_skipped"):
        reasons.append("UNTRACKED_CONTENT_NOT_FULLY_VISIBLE")
    if args.live and not lane_pass:
        reasons.append("AI_CRITIC_LANE_FAILED")
    if not args.live and args.require_live:
        reasons.append("LIVE_CRITIC_REQUIRED")
    if pe_status["status"] in {"COLLAPSED", "SABOTAGE_DETECTED"}:
        reasons.append(f"PE_STATUS_{pe_status['status']}")
    if float(pe_status["p_hall"]) > args.max_p_hall:
        reasons.append("PE_P_HALL_TOO_HIGH")
    if args.commit_message and not args.live and not args.allow_dry_run_commit:
        reasons.append("COMMIT_REQUIRES_LIVE_CRITICS")

    if reasons:
        final_verdict = "FAIL"
        commit_allowed = False
    elif args.live:
        final_verdict = "PASS"
        commit_allowed = True
    else:
        final_verdict = "DRY_RUN_PASS_NOT_COMMITTABLE"
        commit_allowed = False

    commit_result: dict[str, Any] | None = None
    if args.commit_message and commit_allowed:
        if args.stage_all:
            add_result = run_git(["add", "-A"], workspace)
            if add_result["exit_code"] != 0:
                commit_result = {"stage_all": add_result, "commit": None}
                final_verdict = "FAIL"
                commit_allowed = False
                reasons.append("GIT_ADD_FAILED")
        if commit_allowed:
            staged = run_git(["diff", "--name-only", "--cached"], workspace)
            if staged["exit_code"] != 0 or not staged["stdout"]:
                commit_result = {"staged_check": staged, "commit": None}
                final_verdict = "FAIL"
                commit_allowed = False
                reasons.append("NO_STAGED_FILES_FOR_COMMIT")
            else:
                commit = run_git(["commit", "-m", args.commit_message], workspace, timeout=120)
                commit_result = {"staged_files": staged["stdout"].splitlines(), "commit": commit}
                if commit["exit_code"] != 0:
                    final_verdict = "FAIL"
                    commit_allowed = False
                    reasons.append("GIT_COMMIT_FAILED")

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "script_version": SCRIPT_VERSION,
        "workspaceRoot": workspace,
        "mode": "live" if args.live else "dry_run",
        "final_verdict": final_verdict,
        "commit_allowed": commit_allowed,
        "reasons": reasons,
        "changed_files": changed_files,
        "scope": scope,
        "tests": test_results,
        "evidence_quality": quality,
        "perfect_equilibrium": pe_status,
        "lane_summaries": lane_summaries,
        "lanes": lanes,
        "commit_result": commit_result,
    }
    report_path = write_report(report)

    if final_verdict == "FAIL" and not args.no_notify:
        notify_failure(
            "VORTEX Commit Gate FAILED",
            f"{', '.join(reasons[:4]) or 'gate failed'} | report: {report_path}",
        )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"VORTEX commit gate: {final_verdict}")
        print(f"mode: {report['mode']}")
        print(f"commit_allowed: {str(commit_allowed).lower()}")
        print(f"changed_files: {len(changed_files)}")
        print(f"tests_ok: {str(tests_ok).lower()}")
        print(f"PE: p_hall={pe_status['p_hall']} limit={pe_status['p_limit']} status={pe_status['status']}")
        for lane in lane_summaries:
            print(
                f"{lane['name']}: verdict={lane['verdict']} status={lane['status']} "
                f"exit={lane['exit_code']} elapsed={lane['elapsed']}s"
            )
        if reasons:
            print("reasons: " + ", ".join(reasons))
        print(f"report: {report_path}")

    return 0 if final_verdict in {"PASS", "DRY_RUN_PASS_NOT_COMMITTABLE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
