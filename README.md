# data-qd-216 · 飞机载重与重心包络

将机上载荷映射到力臂站位，精确计算总重、力矩和重心，并同时核对站位限重及随总重插值的前后重心包络。

Python 3.11+，业务运行只用标准库。构建固定 setuptools 80.9.0 与 wheel 0.45.1。

    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python --offline -r requirements-build.lock
    uv pip install --python .venv/bin/python --offline --no-build-isolation -e .
    .venv/bin/python -m unittest discover -s tests -v
    .venv/bin/python examples/run_sample.py

本仓只处理调用方提供的合成载重数据，不提供适航或飞行安全认证。详细字段和边界见 docs/contract.md。

## 轨迹仿真样本库

`process` 除静态清单外，还接受 `{"stations": [...], "envelope": [...], "frames": [{"loads": [...]}, ...]}` 场景输入（2–20 帧），对整条轨迹做精确有理判定，返回 `eligible`、`frames`、`violations`。`TrajectoryDataset(path)` 提供可关闭再重开的样本库：`put_scenario` 提交权威版本、`issue` 签发凭据、`get_receipt` 审计、`consume` 领取样本；更正版本会使旧凭据立即失效，所有单次写入均为原子提交。语义详见 docs/contract.md，端到端示例：

    PYTHONPATH=src python3 examples/run_trajectory.py

