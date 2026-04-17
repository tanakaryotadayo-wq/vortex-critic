# Critic Module

Critic モジュールは、VORTEX の read-only 監査レーンです。
DeepSeek 系の自己申告を疑い、diff / exit code / test artifact を優先します。

## 含むもの

- `assets/critic/deepseek-critic.py`
- `assets/critic/vortex-critic.py`
- 各種 `*.config.json`

## 役割

1. prompt を受け取る
2. evidence を集める
3. completion illusion を潰す
4. 次の手を短く返す

