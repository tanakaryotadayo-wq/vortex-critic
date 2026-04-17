# Pipeline① Module

Pipeline① は、OSS リポジトリを packet 化して検査する収集モジュールです。

## 含むもの

- `assets/pipeline/scripts/bootstrap_pipeline_01.sh`
- `assets/pipeline/scripts/pipeline_01_runner.py`
- `assets/pipeline/intelligence/harvest_js_packets.py`
- `assets/pipeline/intelligence/neural_packet.py`
- `assets/pipeline/intelligence/eck_bridge.py`
- `assets/pipeline/gate/cbf.py`

## 役割

1. mount / bootstrap
2. harvest
3. packet 化
4. status 出力

