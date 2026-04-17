# Gemini Bridge Module

Gemini モジュールは、A2A ブリッジと memory pipeline を束ねる実行中枢です。

## 含むもの

- `assets/gemini/gemini_a2a_bridge.py`
- `assets/gemini/memory_pipeline.py`
- `assets/gemini/fleet_bridge.py`
- `assets/gemini/pcc_critic.py`
- `assets/gemini/pcc_critic_standalone.py`

## 役割

1. lane を受ける
2. provider を振り分ける
3. fleet log を記録する
4. KI promotion / recall を回す

