# Pipeline 01

`assets/pipeline/` は OSS packet 化と監査を回す Pipeline① の実装です。

## 役割

- repo を packet 化する
- packet を Neural Packet ledger に保存する
- CBF / n8n / mount 状態をまとめて status に出す
- VORTEX サイドバーから状態を確認できるようにする

## サブディレクトリ

| Path | Purpose |
|---|---|
| `scripts/` | 起動・実行スクリプト |
| `intelligence/` | packet / harvest / bridge モジュール |
| `gate/` | CBF サーバー |
| `integration/` | n8n workflow 定義 |

