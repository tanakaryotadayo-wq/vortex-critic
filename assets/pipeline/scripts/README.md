# Pipeline Scripts

このディレクトリは Pipeline① の起動スクリプトです。

| Script | Purpose |
|---|---|
| `bootstrap_pipeline_01.sh` | rclone mount / CBF / n8n をまとめて起動し、status JSON を書く |
| `pipeline_01_runner.py` | packet harvest → issue packet 化 → レポート生成の runner |

## 実行例

```bash
bash assets/pipeline/scripts/bootstrap_pipeline_01.sh
python3 assets/pipeline/scripts/pipeline_01_runner.py
```
