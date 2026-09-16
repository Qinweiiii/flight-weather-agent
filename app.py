"""
Flask Web API for Multi-Agent Data Query System
提供RESTful API接口供前端调用，支持普通查询和流式SSE查询。
"""

from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import os
import sys
import json
from typing import Dict, Any
from pathlib import Path

# 将当前目录添加到Python路径
sys.path.insert(0, os.path.dirname(__file__))

from agent import MultiAgentSystem
from data.init_memory_db import init_memory_database
from tools.skill_registry import load_skill_registry

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)  # 允许跨域请求，避免浏览器跨域阻断

# 全局系统实例（用于存储不同用户的会话）
user_systems: Dict[str, MultiAgentSystem] = {}
# 让每一个用户有自己的会话上下文，而不是所有人共用一个 Agent 状态


def get_or_create_system(user_id: str) -> MultiAgentSystem:
    """获取或创建用户的系统实例"""
    if user_id not in user_systems:
        system = MultiAgentSystem()
        system.login(user_id)
        user_systems[user_id] = system
    return user_systems[user_id]


@app.route('/')
def index():
    """返回前端页面"""
    return send_from_directory('static', 'index.html')


@app.route('/api/login', methods=['POST'])
def login():
    """用户登录接口"""
    try:
        data = request.json
        user_id = data.get('user_id', 'guest')
        
        # 创建或获取用户系统
        system = get_or_create_system(user_id)

        # 直接从长期记忆数据库加载用户信息（无需等待对话总结）
        ltm = system.master_agent.long_term_memory
        profile = ltm.get_user_profile(user_id)
        preferences = ltm.get_all_preferences(user_id)
        knowledge = ltm.get_all_knowledge(user_id, limit=50)

        return jsonify({
            'success': True,
            'user_id': user_id,
            'session_id': system.session_id,
            'message': f'欢迎 {user_id}！',
            'user_info': {
                'logged_in': True,
                'user_id': user_id,
                'session_id': system.session_id,
                'profile': profile,
                'preferences': preferences,
                'knowledge': knowledge
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/query', methods=['POST'])
def query():
    """查询接口"""
    try:
        data = request.json
        user_id = data.get('user_id', 'guest')
        question = data.get('question', '')
        
        if not question.strip():
            return jsonify({
                'success': False,
                'error': '问题不能为空'
            }), 400
        
        # 获取用户系统
        system = get_or_create_system(user_id)
        
        # 执行查询
        answer = system.query(question)
        
        return jsonify({
            'success': True,
            'answer': answer,
            'user_id': user_id,
            'session_id': system.session_id
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/new_session', methods=['POST'])
def new_session():
    """创建新会话"""
    try:
        data = request.json
        user_id = data.get('user_id', 'guest')
        
        system = get_or_create_system(user_id)
        system.new_session()
        
        return jsonify({
            'success': True,
            'session_id': system.session_id,
            'message': '已开始新会话'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/user_info', methods=['POST'])
def user_info():
    """获取用户信息"""
    try:
        data = request.json
        user_id = data.get('user_id', 'guest')
        
        system = get_or_create_system(user_id)
        # 直接读取长期记忆，包括知识列表
        ltm = system.master_agent.long_term_memory
        info = system.get_user_info()
        info['knowledge'] = ltm.get_all_knowledge(user_id, limit=50)

        return jsonify({
            'success': True,
            'user_info': info
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/reset_memory', methods=['POST'])
def reset_memory():
    """重置长期记忆库（清空所有用户偏好与知识）"""
    try:
        data = request.json or {}
        if not data.get('confirm', False):
            return jsonify({
                'success': False,
                'error': '请确认重置操作（confirm=true）'
            }), 400

        # 优先使用现有系统实例中的配置路径，避免与配置文件不一致
        memory_db_path = './data/long_term_memory.db'
        if user_systems:
            sample_system = next(iter(user_systems.values()))
            memory_db_path = sample_system.master_agent.long_term_memory.db_path

        db_file = Path(memory_db_path)

        # 清理内存中的系统实例，避免持有旧连接
        user_systems.clear()

        if db_file.exists():
            db_file.unlink()

        init_memory_database(str(db_file))

        return jsonify({
            'success': True,
            'message': '记忆库已重置',
            'memory_db': str(db_file)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/query_stream', methods=['POST'])
def query_stream():
    """流式查询接口（Server-Sent Events）
    
    前端使用 fetch + ReadableStream 接收，实现逐字打字效果。
    事件类型：
      - status: 处理状态更新（如"正在查询数据库..."）
            - plan: Planner Agent 生成的任务计划
            - trace: Agent 执行轨迹事件
      - intent: 识别到的意图类型
      - sql: 生成的SQL语句（含重试次数）
      - sources: 联网搜索来源URL列表
      - chart: ECharts图表配置JSON
      - chunk: LLM输出的文字片段（流式）
      - error: 错误信息（非致命，继续处理）
      - done: 流结束标志（含完整answer）
    """
    try:
        data = request.json
        user_id = data.get('user_id', 'guest')
        question = data.get('question', '')
        
        if not question.strip():
            return jsonify({'success': False, 'error': '问题不能为空'}), 400
        
        system = get_or_create_system(user_id)
        
        def generate():
            try:
                for event in system.stream_query(question):
                    yield event
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'answer': f'系统错误: {str(e)}'})}\n\n"
        
        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive'
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/health', methods=['GET'])
def health():
    """健康检查接口"""
    search_available = False
    agent_features = {
        'planner_agent': False,
        'tool_router_agent': False,
        'critic_agent': False,
        'guardrail_agent': False,
        'memory_agent': False,
        'debate_agent': False
    }
    try:
        if user_systems:
            first_system = next(iter(user_systems.values()))
            master = first_system.master_agent
            search_available = master.search_agent.available
            agent_features = {
                'planner_agent': hasattr(master, 'planner_agent'),
                'tool_router_agent': hasattr(master, 'tool_router_agent'),
                'critic_agent': hasattr(master, 'critic_agent'),
                'guardrail_agent': hasattr(master, 'guardrail_agent'),
                'memory_agent': hasattr(master, 'memory_agent'),
                'debate_agent': hasattr(master, 'debate_agent')
            }
    except Exception:
        pass
    
    return jsonify({
        'status': 'healthy',
        'active_users': len(user_systems),
        'features': {
            'sql_self_correction': True,
            'sql_readonly_guard': True,
            'execution_latency_trace': True,
            'streaming': True,
            'web_search': search_available,
            'data_visualization': True,
            **agent_features
        },
        'agent_version': 'v3.1-agentic'
    })


@app.route('/api/skills', methods=['GET'])
def skills():
    """返回声明式 Skill Registry，便于前端或面试演示查看系统能力边界。"""
    try:
        registry = load_skill_registry(Path(__file__).parent / "config" / "skill_registry.yaml")
        return jsonify({
            'success': True,
            'registry': registry.summary()
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


if __name__ == '__main__':
    # 检查环境变量
    if not os.getenv("DASHSCOPE_API_KEY"):
        print("错误：未设置 DASHSCOPE_API_KEY 环境变量")
        sys.exit(1)
    
    port = int(os.getenv("PORT", "5000"))

    print("🚀 多智能体数据查询系统 Web API 启动中...")
    print(f"📡 访问地址: http://localhost:{port}")
    
    app.run(host='0.0.0.0', port=port, debug=True)
