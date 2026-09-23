"""Local operations workbench API. Demo identity is not production authentication."""
import json
import os
from pathlib import Path
import re
import secrets
import threading
import queue
from flask import Flask, request, jsonify, send_from_directory, Response, session
from agent import MultiAgentSystem
from tools.skill_registry import load_skill_registry
from tools.metrics import provenance
from tools.sql_executor import read_rows

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=str(ROOT/'static'), static_url_path='')
app.config.update(SECRET_KEY=os.getenv('FLASK_SECRET_KEY') or secrets.token_hex(32),
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict', MAX_CONTENT_LENGTH=32768)
user_systems = {}
system_locks = {}
creation_lock = threading.Lock()


def get_or_create_system(user_id):
    with creation_lock:
        if user_id not in user_systems:
            if len(user_systems) >= 32:
                raise ValueError('本地会话数已达上限，请重启服务。')
            system = MultiAgentSystem()
            if not system.login(user_id):
                raise ValueError('无法初始化用户记忆')
            user_systems[user_id] = system
            system_locks[user_id] = threading.Lock()
        return user_systems[user_id]


def payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('请求必须是 JSON 对象')
    return data


def current_system():
    user = session.get('user_id')
    if not user:
        return None
    return get_or_create_system(user)


@app.before_request
def check_session():
    if request.path.startswith('/api/') and request.path not in ('/api/health', '/api/skills', '/api/login', '/api/dataset'):
        if not session.get('user_id'):
            return jsonify(success=False, error='请先进入工作台'), 401
        data = request.get_json(silent=True)
        if isinstance(data, dict) and data.get('user_id', session['user_id']) != session['user_id']:
            return jsonify(success=False, error='会话用户不匹配'), 403


@app.errorhandler(ValueError)
def bad_request(exc):
    return jsonify(success=False, error=str(exc)), 400


@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/api/login', methods=['POST'])
def login():
    user = payload().get('user_id', 'guest')
    if not isinstance(user, str) or not re.fullmatch(r'[\w\-]{1,64}', user):
        raise ValueError('用户标识使用 1–64 个字母、数字、汉字、下划线或连字符')
    system = get_or_create_system(user)
    session['user_id'] = user
    info = system.get_user_info()
    info['knowledge'] = system.master_agent.long_term_memory.get_all_knowledge(user, limit=50)
    return jsonify(success=True, user_id=user, session_id=system.session_id, user_info=info,
                   mode='demo' if system.demo else 'live')


@app.route('/api/user_info', methods=['POST'])
def user_info():
    system = current_system()
    info = system.get_user_info()
    info['knowledge'] = system.master_agent.long_term_memory.get_all_knowledge(system.user_id, limit=50)
    return jsonify(success=True, user_info=info)


@app.route('/api/new_session', methods=['POST'])
def new_session():
    system = current_system()
    lock = system_locks[system.user_id]
    if not lock.acquire(blocking=False):
        return jsonify(success=False, error='当前分析尚未结束'), 409
    try:
        system.new_session()
        return jsonify(success=True, session_id=system.session_id)
    finally:
        lock.release()


@app.route('/api/reset_memory', methods=['POST'])
def reset_memory():
    if payload().get('confirm') is not True:
        raise ValueError('需要 confirm=true')
    system = current_system()
    lock = system_locks[system.user_id]
    if not lock.acquire(blocking=False):
        return jsonify(success=False, error='当前分析尚未结束'), 409
    try:
        system.master_agent.long_term_memory.clear_user_memory(system.user_id)
        system.new_session()
        return jsonify(success=True, message='已清除当前用户记忆', session_id=system.session_id)
    finally:
        lock.release()


def query_input():
    q = payload().get('question', '')
    if not isinstance(q, str) or not q.strip() or len(q) > 8000:
        raise ValueError('问题长度需为 1–8000 字符')
    system = current_system()
    lock = system_locks[system.user_id]
    return q.strip(), system, lock


@app.route('/api/query', methods=['POST'])
def query():
    q, system, lock = query_input()
    if not lock.acquire(blocking=False):
        return jsonify(success=False, error='同一用户正在分析，请等待完成'), 409
    try:
        final = None
        for frame in system.stream_query(q):
            event = json.loads(frame[6:])
            if event['type'] == 'done':
                final = event
        return jsonify(success=not bool(final.get('failure_stage')), answer=final['answer'],
                       request_id=final['request_id'], failure_stage=final.get('failure_stage'), session_id=system.session_id)
    finally:
        lock.release()


@app.route('/api/query_stream', methods=['POST'])
def query_stream():
    q, system, lock = query_input()
    if not lock.acquire(blocking=False):
        return jsonify(success=False, error='同一用户正在分析，请等待完成'), 409
    mailbox = queue.Queue(maxsize=64)
    cancelled = threading.Event()
    def put(item):
        while not cancelled.is_set():
            try:
                mailbox.put(item, timeout=1)
                return
            except queue.Full:
                continue
    def work():
        try:
            for frame in system.stream_query(q):
                if cancelled.is_set():
                    break
                put(frame)
        finally:
            lock.release()
            put(None)
    threading.Thread(target=work, daemon=True, name='flight-query').start()
    def generate():
        try:
            while True:
                try:
                    frame = mailbox.get(timeout=10)
                except queue.Empty:
                    yield ': keepalive\n\n'
                    continue
                if frame is None:
                    break
                yield frame
        finally:
            cancelled.set()
    response = Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
    response.call_on_close(cancelled.set)
    return response


@app.route('/api/skills')
def skills():
    return jsonify(success=True, registry=load_skill_registry(ROOT/'config/skill_registry.yaml').summary())


@app.route('/api/dataset')
def dataset():
    import yaml
    config = yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
    mode = os.getenv('APP_MODE', 'live')
    path = ROOT/os.getenv('FLIGHT_DB_PATH', 'data/demo_operations.db' if mode=='demo' else config['database']['path'])
    if not path.is_file():
        return jsonify(success=False, error='数据库未初始化', mode=mode), 503
    stats = read_rows(path, 'SELECT COUNT(*) AS flight_cnt, MIN(FL_DATE) AS min_date, MAX(FL_DATE) AS max_date FROM flights_enriched')[0]
    return jsonify(success=True, mode=mode, dataset={**stats, **provenance(path)})


@app.route('/api/health')
def health():
    return jsonify(status='ok', mode=os.getenv('APP_MODE', 'live'),
                   llm_configured=bool(os.getenv('DASHSCOPE_API_KEY')),
                   search_configured=bool(os.getenv('TAVILY_API_KEY')),
                   note='进程存活检查；未验证模型权限或外部网络', agent_version='4.0-controlled-workflow')


if __name__ == '__main__':
    app.run(host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '5001')), debug=False, threaded=True)
