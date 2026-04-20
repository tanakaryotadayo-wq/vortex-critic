#!/usr/bin/env python3
"""
pcc-critic — True 9-Axis PCC-Vortex Critic Integration
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# 動的に真の PCC Router をインポートする
FUSION_GATE_PATH = os.path.expanduser("~/fusion-gate")
if FUSION_GATE_PATH not in sys.path:
    sys.path.insert(0, FUSION_GATE_PATH)

try:
    from gate.pcc_router import PCCRouter, audit_output
    HAS_TRUE_PCC = True
except ImportError as e:
    print(f"Warning: Could not import true PCCRouter from {FUSION_GATE_PATH}: {e}", file=sys.stderr)
    HAS_TRUE_PCC = False

# フォールバック用およびVortex拡張プリセット
EXTENDED_PRESETS = {
    "探": "#探",  # 525955895
    "極": "#極",  # 998598118
    "均": "#均",  # 555555555
    "監": "999199019", # 監査特化（#鬼相当）
    "刃": "875897725", # 実装設計レビュー（#匠相当）
}

MODEL_ROUTING = {
    "fast":   "gemini-2.5-flash",
    "standard": "gemini-2.5-pro",
    "deep":   "gemini-3.1-pro-preview",
}


# ── Evidence Collection (VORTEX Core) ────────────────────────────────────────

def collect_git_evidence(workspace_root: str) -> dict[str, Any]:
    """Collect objective evidence from git state."""
    evidence: dict[str, Any] = {}
    try:
        result = subprocess.run(["git", "diff", "--stat", "HEAD"], cwd=workspace_root, capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0 and result.stdout.strip():
            evidence["diff_stat"] = result.stdout.strip()
    except Exception: pass

    try:
        result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=workspace_root, capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0 and result.stdout.strip():
            evidence["changed_files"] = result.stdout.strip().split("\n")
    except Exception: pass

    try:
        result = subprocess.run(["git", "diff", "--name-only", "--cached"], cwd=workspace_root, capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0 and result.stdout.strip():
            evidence["staged_files"] = result.stdout.strip().split("\n")
    except Exception: pass

    try:
        result = subprocess.run(["git", "log", "-1", "--oneline"], cwd=workspace_root, capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0 and result.stdout.strip():
            evidence["last_commit"] = result.stdout.strip()
    except Exception: pass

    return evidence

def collect_test_evidence(workspace_root: str) -> dict[str, Any]:
    """Check for recent test results or common test artifacts."""
    evidence: dict[str, Any] = {}
    ws = Path(workspace_root)
    test_artifacts = ["test-results.xml", "junit.xml", ".pytest_cache/lastfailed", "coverage/lcov.info", "coverage/coverage-summary.json"]
    found = [str(a) for a in test_artifacts if (ws / a).exists()]
    if found:
        evidence["test_artifacts_found"] = found
    return evidence


# ── True PCC Logic ──────────────────────────────────────────────────────────

def get_coordinate(preset_name: str) -> str:
    """プリセット名から9桁座標を解決する"""
    mapped = EXTENDED_PRESETS.get(preset_name, f"#{preset_name}")
    if HAS_TRUE_PCC:
        coord = PCCRouter._resolve_base(mapped)
        if coord: return coord
        return dict(PCCRouter.PRESETS).get("#探", "525955895")
    else:
        return "525955895"

def inject_pcc(prompt: str, preset: str, evidence: dict[str, Any], data: dict[str, Any]) -> tuple[str, str]:
    """真のPCCルーターを用いてプロトコル制約を生成し、VORTEX証拠データと結合して注入する"""
    coord = get_coordinate(preset)
    
    if HAS_TRUE_PCC:
        router = PCCRouter()
        parsed = router.parse_input(f"e:{coord}")
        protocol = router.generate_protocol(parsed)
    else:
        protocol = f"<PCC_Protocol>\nFallback constraints for {preset}\n</PCC_Protocol>"
        
    context_lines = []
    active_file = data.get("activeFile")
    workspace_root = data.get("workspaceRoot")

    if isinstance(active_file, str) and active_file:
        context_lines.append(f"Active file: {active_file}")
    if isinstance(workspace_root, str) and workspace_root:
        context_lines.append(f"Workspace root: {workspace_root}")

    if evidence:
        context_lines.append("\n--- Objective Evidence (collected by VORTEX, not self-reported) ---")
        if "diff_stat" in evidence:
            context_lines.append(f"Git diff stat:\n{evidence['diff_stat']}")
        if "changed_files" in evidence:
            context_lines.append(f"Changed files: {', '.join(evidence['changed_files'])}")
        if "staged_files" in evidence:
            context_lines.append(f"Staged files: {', '.join(evidence['staged_files'])}")
        if "last_commit" in evidence:
            context_lines.append(f"Last commit: {evidence['last_commit']}")
        if "test_artifacts_found" in evidence:
            context_lines.append(f"Test artifacts found: {', '.join(evidence['test_artifacts_found'])}")
            
        if "test_exit_code" in data:
            context_lines.append(f"Test exit code: {data['test_exit_code']}")
        if "lint_exit_code" in data:
            context_lines.append(f"Lint exit code: {data['lint_exit_code']}")
        if "scope_files" in data:
            context_lines.append(f"Intended scope: {', '.join(data['scope_files'])}")
    else:
        context_lines.append("⚠️ NO OBJECTIVE EVIDENCE FOUND. Worker has not run any verification.")

    context_block = "\n".join(context_lines)
    if context_block:
        context_block = f"Known context:\n{context_block}\n\n"

    enriched = f"{protocol}\n---\n{context_block}\nReview Request:\n{prompt}"
    return enriched, coord

def run_gemini(enriched_prompt: str, model: str, timeout: int = 120) -> dict:
    env = os.environ.copy()
    homebrew_node = "/opt/homebrew/Cellar/node/25.3.0/bin"
    if os.path.exists(homebrew_node):
        env['PATH'] = f"{homebrew_node}:/opt/homebrew/bin:{env.get('PATH', '')}"
    else:
        nvm_dir = os.path.expanduser("~/.nvm")
        node_path = os.popen(f'bash -c "source {nvm_dir}/nvm.sh && nvm which node 2>/dev/null"').read().strip()
        if node_path:
            env['PATH'] = f"{os.path.dirname(node_path)}:{env.get('PATH', '')}"

    gemini_bin = "/opt/homebrew/bin/gemini"
    if not os.path.exists(gemini_bin):
        gemini_bin = "gemini"

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            [gemini_bin, '--approval-mode', 'plan', '-p', enriched_prompt, '-m', model],
            capture_output=True, text=True, env=env, timeout=timeout,
            cwd=os.path.expanduser("~"),
        )
        elapsed = time.monotonic() - t0
        return {
            "text": result.stdout.strip() or result.stderr.strip(),
            "exit_code": result.returncode,
            "elapsed": round(elapsed, 1),
            "model": model,
        }
    except subprocess.TimeoutExpired:
        return {
            "text": "",
            "exit_code": -1,
            "elapsed": timeout,
            "model": model,
            "error": "TIMEOUT",
        }

def audit_response(text: str, coord: str) -> dict:
    """真のPCC監査エンジン（9軸ベクトルドリフト検知）を用いて評価する"""
    if not text or len(text.strip()) < 20:
        return {"verdict": "NO_OP", "score": 0.0, "violations": [], "words": 0}

    words = len(text.split())
    
    if HAS_TRUE_PCC:
        audit_res = audit_output(text, coord)
        score = audit_res["score"]
        violations = audit_res["violations"]
        compliant = audit_res["compliant"]
        
        if compliant:
            verdict = "PASS"
        elif score >= 0.8:
            verdict = "REVIEW"
        elif score < 0.5:
            verdict = "FAIL"
        else:
            verdict = "NEEDS_EVIDENCE"
            
        return {
            "verdict": verdict,
            "score": score,
            "violations": violations,
            "words": words,
            "evidence_count": len(violations)  # Legacy compat
        }
    else:
        return {"verdict": "PASS", "score": 1.0, "violations": [], "words": words}

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

def main():
    parser = argparse.ArgumentParser(description="True 9-Axis PCC-Vortex Critic Pipeline")
    parser.add_argument("prompt", nargs="?", help="プロンプト（JSON stdinからも読める）")
    parser.add_argument("--preset", "-P", default="探", help="PCC プリセット名（探, 極, 監, 刃 など）")
    parser.add_argument("--model", "-m", default="deep", help="モデル名 or ショートカット")
    parser.add_argument("--timeout", "-t", type=int, default=120, help="タイムアウト秒")
    parser.add_argument("--json", "-j", action="store_true", help="JSON 出力")
    parser.add_argument("--audit-only", action="store_true", help="stdin のテキストを絶対座標で監査")
    args = parser.parse_args()

    data = read_stdin_json()
    stdin_text = data.get("prompt", "") if data else ""
    
    coord = get_coordinate(args.preset)

    if args.audit_only:
        text = stdin_text or (args.prompt or "")
        audit = audit_response(text, coord)
        if args.json:
            print(json.dumps(audit, ensure_ascii=False, indent=2))
        else:
            for k, v in audit.items():
                print(f"  {k}: {v}")
        sys.exit(0 if audit["verdict"] == "PASS" else 1)

    prompt = args.prompt or ""
    if data and "prompt" in data:
        prompt = data["prompt"]

    if not prompt:
        parser.error("プロンプトを指定するか、JSONをパイプしてください")

    # Collect objective evidence from workspace (VORTEX Core)
    workspace_root = data.get("workspaceRoot", "")
    evidence: dict[str, Any] = {}
    if workspace_root and Path(workspace_root).is_dir():
        evidence.update(collect_git_evidence(workspace_root))
        evidence.update(collect_test_evidence(workspace_root))

    model = MODEL_ROUTING.get(args.model, args.model)
    enriched, active_coord = inject_pcc(prompt, args.preset, evidence, data)

    if not args.json:
        print(f"[PCC] Preset: {args.preset} / Active Coordinate: {active_coord}")
        print(f"[Model] {model} | [Prompt size] {len(enriched)} chars")
        if evidence:
            print(f"[Evidence] Diff Stat/Changed files/Test metrics collected from {workspace_root}")
        print("─" * 50)

    result = run_gemini(enriched, model, args.timeout)
    audit = audit_response(result["text"], active_coord)

    if args.json:
        # Compatibility with vortex-critic expected format
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": f"[PCC Critic]\n{result['text'].strip()}"
            },
            "pcc_preset": args.preset,
            "coordinate": active_coord,
            "model": model,
            "response": result["text"],
            "elapsed": result["elapsed"],
            "exit_code": result["exit_code"],
            "audit": audit,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(result["text"])
        print("─" * 50)
        print(f"VERDICT: {audit['verdict']} | Score: {audit['score']} | Violations: {len(audit.get('violations', []))}")
        if audit.get("violations"):
            print("Drift Violations:")
            for v in audit["violations"]:
                print(f"  - Axis {v['axis']} ({v['value']}): {v['issue']} (Matched: '{v['pattern']}')")

    sys.exit(0 if audit["verdict"] in ["PASS", "REVIEW"] else 1)

if __name__ == "__main__":
    main()
