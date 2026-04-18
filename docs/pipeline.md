# Pipeline① Module

## 定義

Pipeline① は、OSS repo を**packet 化して監査用 artifacts に落とすバッチ系モジュール**です。  
常時対話用ではなく、状態を `status.json` に書き出しながら進む運用パイプラインです。

## 管轄ファイル

| File | Role |
|---|---|
| `assets/pipeline/scripts/bootstrap_pipeline_01.sh` | mount / CBF / n8n の bootstrap |
| `assets/pipeline/scripts/pipeline_01_runner.py` | packetize -> analysis -> issue packet persist |
| `assets/pipeline/intelligence/harvest_js_packets.py` | repo を Neural Packet 化 |
| `assets/pipeline/intelligence/neural_packet.py` | packet / ledger モデル |
| `assets/pipeline/intelligence/eck_bridge.py` | ECK bridge |
| `assets/pipeline/gate/cbf.py` | CBF server |

## bootstrap の厳密契約

### 入力

主に環境変数で受けます。

| Variable | Purpose |
|---|---|
| `PIPELINE_01_STATE_DIR` | state 出力先 |
| `PIPELINE_01_STATUS_FILE` | status JSON path |
| `PIPELINE_01_MOUNT_PATH` | rclone mount 先 |
| `PIPELINE_01_RCLONE_REMOTE` / `PIPELINE_01_RCLONE_SUBPATH` | drive mount 元 |
| `PIPELINE_01_N8N_COMPOSE` | compose file |
| `PIPELINE_01_WORKFLOW_JSON` | workflow 定義 |

### 出力

`status.json` に最低限次を書きます。

- `pipeline`
- `stage`
- `mounted`
- `cbfHealthy`
- `n8nReady`
- `mountError`
- `rcloneLog`

### 失敗コード

mount の代表的な失敗値:

- `rclone_not_found`
- `mount_not_visible`

## runner の厳密契約

### CLI 引数

`pipeline_01_runner.py` は次を受けます。

- `--repo-path`
- `--repo-name`
- `--state-dir`
- `--packet-db`
- `--issue-db`
- `--status-path`
- `--mount-path`
- `--drive-remote`
- `--drive-subpath`
- `--claude-provider`
- `--gemini-provider`
- `--pcc`
- `--allow-empty-packets`

### 実行ステージ

runner は `status["stage"]` を進めながら動きます。

1. `starting`
2. `packetizing`
3. `claude_analysis`
4. `gemini_issue_split`
5. `eck_persistence`
6. `completed`
7. `failed`

### 永続成果物

| Artifact | 内容 |
|---|---|
| `status.json` | 現在状態 |
| `packet_db` | harvested packets |
| `issue_db` | issue packets |
| `claude_analysis.json` | Claude 側分析 |
| `gemini_issue_candidates.json` | Gemini 側 issue candidate |
| `issue_packets.jsonl` | issue packet export |

## fail-closed 契約

`packet_summary.count <= 0` のとき、`--allow-empty-packets` が無ければ**失敗**にします。  
これは「ゼロ件でも成功扱いにしてしまう」挙動を防ぐためです。

## 依存

| Dependency | 用途 |
|---|---|
| `harvest_js_packets.py` | repo packetization |
| Fusion Gate | Claude / Gemini provider invoke |
| CBF server | step 記録 |
| SQLite | packet / issue ledger |
| rclone / docker compose | bootstrap 周辺 |

## 非責務

- VS Code の UI 表示
- conversation lane routing
- KI queue 運用

## 改修ルール

1. stage 名を変えるなら、**status reader 側も必ず合わせる**。  
2. 失敗時に `status.json` を書かずに落ちる挙動は作らない。  
3. packetizer を変えるときは、**ゼロ packet fail-closed** を維持する。  
