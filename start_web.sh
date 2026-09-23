#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

echo "====================================="
echo " 多智能体数据查询系统 - Web界面启动"
echo "====================================="
echo

# Resolve Python interpreter. Prefer the project virtualenv, then python3.
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
else
    echo "[错误] 未找到 Python。请先安装 Python 3。"
    exit 1
fi

# 检查环境变量
if [ "${APP_MODE:-live}" != "demo" ] && [ -z "${QWEN_API_KEY:-}" ] && [ -z "${DASHSCOPE_API_KEY:-}" ]; then
    echo "[错误] 未设置 QWEN_API_KEY 环境变量"
    echo
    echo "请先设置环境变量："
    echo "export QWEN_API_KEY=your_api_key"
    echo
    echo "旧变量 DASHSCOPE_API_KEY 仍兼容。"
    echo
    exit 1
fi

echo "运行模式: ${APP_MODE:-live}"
echo

# 检查数据库是否存在
if [ "${APP_MODE:-live}" = "demo" ] && [ ! -f "${FLIGHT_DB_PATH:-data/demo_operations.db}" ]; then
    "$PYTHON" data/generate_demo_data.py --db "${FLIGHT_DB_PATH:-data/demo_operations.db}"
elif [ "${APP_MODE:-live}" != "demo" ] && [ ! -f "${FLIGHT_DB_PATH:-data/flight_weather.db}" ]; then
    echo "[警告] 业务数据库不存在，正在初始化..."
    "$PYTHON" data/init_flight_weather_db.py --db "${FLIGHT_DB_PATH:-data/flight_weather.db}"
    echo "业务数据库初始化完成"
    echo
fi

# LongTermMemory creates the configured per-environment database on first use.

echo "数据库检查完成"
echo

echo "正在启动Web服务器..."
echo
export PORT="${PORT:-5001}"
echo "访问地址: http://localhost:$PORT"
echo
echo "按 Ctrl+C 停止服务器"
echo "====================================="
echo

exec "$PYTHON" app.py
