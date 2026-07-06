import os
import json
import glob
import sqlite3
import re
from .models import Session

def get_mtime(path):
    try:
        return os.path.getmtime(path) * 1000
    except OSError:
        return 0

def parse_opencode():
    sessions = []
    db_path = os.path.expanduser('~/.local/share/opencode/opencode.db')
    if not os.path.exists(db_path):
        return sessions
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT s.time_updated, s.title, 
                   COALESCE(NULLIF(s.directory, ''), w.directory), s.id 
            FROM session s 
            LEFT JOIN workspace w ON s.workspace_id = w.id
        """)
        for row in cur.fetchall():
            ts, title, path, session_id = row
            if ts and path:
                sessions.append(Session(timestamp=float(ts), agent='OpenCode', path=path, title=title or '', session_id=session_id or '', tool_name='opencode'))
    except Exception:
        pass
    return sessions

def parse_claude():
    sessions = []
    base_dir = os.path.expanduser('~/.claude/projects/')
    if not os.path.exists(base_dir):
        return sessions
    try:
        for d in os.listdir(base_dir):
            dp = os.path.join(base_dir, d)
            if os.path.isdir(dp) and d.startswith('-'):
                # Try to extract the real path from the latest jsonl
                path = ''
                jsonl_files = glob.glob(os.path.join(dp, '*.jsonl'))
                mtime = 0
                session_id = ''
                if jsonl_files:
                    latest_jsonl = max(jsonl_files, key=get_mtime)
                    mtime = get_mtime(latest_jsonl)
                    session_id = os.path.basename(latest_jsonl).replace('.jsonl', '')
                    
                    try:
                        with open(latest_jsonl) as f:
                            for _ in range(20):
                                line = f.readline()
                                if not line:
                                    break
                                if '"cwd":"' in line:
                                    # Fallback simple regex
                                    match = re.search(r'"cwd":"([^"]+)"', line)
                                    if match:
                                        path = match.group(1).replace('\\\\', '\\')
                                        break
                    except:
                        pass
                else:
                    mtime = get_mtime(dp)
                
                # If we couldn't find cwd in jsonl, fallback to the replacement logic
                if not path:
                    inner = d
                    if inner.startswith('-'):
                        inner = inner[1:]
                    path = '/' + inner.replace('-', '/')

                title = ''
                if mtime > 0:
                    # Attempt to extract title from latest jsonl
                    if jsonl_files:
                        try:
                            with open(latest_jsonl) as f:
                                for line in f:
                                    try:
                                        data = json.loads(line)
                                        if data.get('type') == 'user':
                                            msg = data.get('message', {})
                                            msg_content = msg.get('content', '')
                                            text = ''
                                            if isinstance(msg_content, str):
                                                text = msg_content
                                            elif isinstance(msg_content, list):
                                                for block in msg_content:
                                                    if block.get('type') == 'text':
                                                        text = block.get('text', '')
                                                        break
                                            if text:
                                                text = re.sub(r'<[^>]+>', '', text).strip()
                                                text = ' '.join(text.split())
                                                title = text[:50] + ('...' if len(text) > 50 else '')
                                                break
                                    except:
                                        pass
                        except:
                            pass
                    sessions.append(Session(timestamp=mtime, agent='Claude Code', path=path, title=title, session_id=session_id, tool_name='claude'))
    except Exception:
        pass
    return sessions

def parse_gemini():
    sessions = []
    projects_json = os.path.expanduser('~/.gemini/projects.json')
    if not os.path.exists(projects_json):
        return sessions
    try:
        with open(projects_json) as f:
            data = json.load(f)
            for path, hash_dir in data.get('projects', {}).items():
                tmp_dir = os.path.expanduser(f'~/.gemini/tmp/{hash_dir}')
                if os.path.exists(tmp_dir):
                    files = glob.glob(os.path.join(tmp_dir, '*'))
                    mtime = max((get_mtime(f) for f in files), default=get_mtime(tmp_dir))
                    title = ''
                    log_path = os.path.join(tmp_dir, 'logs.json')
                    if os.path.exists(log_path):
                        try:
                            with open(log_path) as f:
                                logs = json.load(f)
                                for entry in logs:
                                    if entry.get('type') == 'user':
                                        msg = entry.get('message', '').strip()
                                        if msg:
                                            # Clean up newlines
                                            msg = ' '.join(msg.split())
                                            title = msg[:50] + ('...' if len(msg) > 50 else '')
                                            break
                        except:
                            pass
                    sessions.append(Session(timestamp=mtime, agent='Gemini CLI', path=path, title=title, session_id='', tool_name='gemini'))
    except Exception:
        pass
    return sessions

def parse_antigravity():
    sessions = []
    brain_dir = os.path.expanduser('~/.gemini/antigravity/brain/')
    if not os.path.exists(brain_dir):
        return sessions
    try:
        for d in os.listdir(brain_dir):
            dp = os.path.join(brain_dir, d)
            if not os.path.isdir(dp):
                continue
            
            mdata_files = glob.glob(os.path.join(dp, '*.metadata.json'))
            mtime = 0
            title = ''
            for mf in mdata_files:
                mt = get_mtime(mf)
                if mt > mtime:
                    mtime = mt
                    try:
                        with open(mf) as f:
                            data = json.load(f)
                            title = data.get('summary', title)
                    except:
                        pass
            
            if mtime == 0:
                mtime = get_mtime(dp)
                
            path = ''
            overview_path = os.path.join(dp, '.system_generated', 'logs', 'overview.txt')
            if os.path.exists(overview_path):
                try:
                    with open(overview_path) as f:
                        content = f.read()
                        match = re.search(r'"Cwd":"\\?"([^"\\]+)', content)
                        if match:
                            path = match.group(1)
                except:
                    pass
                    
            if path:
                sessions.append(Session(timestamp=mtime, agent='Antigravity', path=path, title=title, session_id='', tool_name='gemini'))
    except Exception:
        pass
    return sessions

def parse_codex():
    sessions = []
    base_dir = os.path.expanduser('~/.codex/sessions/')
    if not os.path.exists(base_dir):
        return sessions
    try:
        for jsonl in glob.glob(os.path.join(base_dir, '*', '*', '*', '*.jsonl')):
            mtime = get_mtime(jsonl)
            session_id = os.path.basename(jsonl).replace('.jsonl', '')
            path = ''
            title = ''
            try:
                with open(jsonl) as f:
                    for line in f:
                        if not line:
                            break
                        try:
                            data = json.loads(line)
                            if data.get('type') == 'session_meta' and 'payload' in data:
                                if not path:
                                    path = data['payload'].get('cwd', '')
                            elif data.get('type') == 'response_item':
                                payload = data.get('payload', {})
                                if payload.get('type') == 'message' and payload.get('role') == 'user':
                                    content = payload.get('content', [])
                                    if isinstance(content, list):
                                        for block in content:
                                            if block.get('type') == 'input_text':
                                                text = block.get('text', '')
                                                if text:
                                                    text = re.sub(r'<[^>]+>', '', text).strip()
                                                    text = ' '.join(text.split())
                                                    title = text[:80] + ('...' if len(text) > 80 else '')
                                                    break
                                    elif isinstance(content, str):
                                        text = content
                                        if text:
                                            text = re.sub(r'<[^>]+>', '', text).strip()
                                            text = ' '.join(text.split())
                                            title = text[:80] + ('...' if len(text) > 80 else '')
                                    if title:
                                        break
                        except:
                            pass
                        if title:
                            break
            except:
                pass
                
            if path:
                sessions.append(Session(timestamp=mtime, agent='Codex CLI', path=path, title=title, session_id=session_id, tool_name='codex'))
    except Exception:
        pass
    return sessions

def parse_cursor():
    sessions = []
    projects_dir = os.path.expanduser('~/.cursor/projects')
    if not os.path.exists(projects_dir):
        return sessions
    home = os.path.expanduser('~')
    try:
        for enc_dir in os.listdir(projects_dir):
            transcripts_dir = os.path.join(projects_dir, enc_dir, 'agent-transcripts')
            if not os.path.isdir(transcripts_dir):
                continue
            for uuid_dir in os.listdir(transcripts_dir):
                uuid_path = os.path.join(transcripts_dir, uuid_dir)
                if not os.path.isdir(uuid_path):
                    continue
                jsonl_path = os.path.join(uuid_path, f'{uuid_dir}.jsonl')
                if not os.path.isfile(jsonl_path):
                    continue
                mtime = get_mtime(jsonl_path)
                if mtime == 0:
                    continue
                title = ''
                all_paths = []
                try:
                    with open(jsonl_path) as f:
                        for line in f:
                            if not line.strip():
                                continue
                            data = json.loads(line)
                            role = data.get('role', '')
                            content = data.get('message', {}).get('content', '')
                            if role == 'user' and not title:
                                if isinstance(content, list):
                                    for block in content:
                                        if block.get('type') == 'text':
                                            text = block.get('text', '')
                                            text = re.sub(r'<[^>]+>', '', text).strip()
                                            text = ' '.join(text.split())
                                            title = text[:80] + ('...' if len(text) > 80 else '')
                                            break
                                elif isinstance(content, str):
                                    text = re.sub(r'<[^>]+>', '', content).strip()
                                    text = ' '.join(text.split())
                                    title = text[:80] + ('...' if len(text) > 80 else '')
                            if isinstance(content, list):
                                for block in content:
                                    inp = block.get('input', {})
                                    if isinstance(inp, dict):
                                        p = inp.get('path', '')
                                        if p and p.startswith(home):
                                            all_paths.append(p)
                            if title and len(all_paths) >= 5:
                                break
                except Exception:
                    pass
                path = ''
                if all_paths:
                    try:
                        path = os.path.commonpath(all_paths)
                    except ValueError:
                        pass
                if not path:
                    path = enc_dir
                sessions.append(Session(
                    timestamp=mtime,
                    agent='Cursor',
                    path=path,
                    title=title,
                    session_id=uuid_dir,
                    tool_name='cursor',
                ))
    except Exception:
        pass
    return sessions


def parse_pi():
    sessions = []
    base_dir = os.path.expanduser('~/.pi/agent/sessions/')
    if not os.path.exists(base_dir):
        return sessions
    try:
        for d in os.listdir(base_dir):
            if d.startswith('--') and d.endswith('--'):
                inner = d[2:-2]
                if inner.startswith('-'):
                    inner = inner[1:]
                
                dp = os.path.join(base_dir, d)
                jsonl_files = glob.glob(os.path.join(dp, '*.jsonl'))
                
                path = ''
                title = ''
                session_id = ''
                if jsonl_files:
                    latest_jsonl = max(jsonl_files, key=get_mtime)
                    mtime = get_mtime(latest_jsonl)
                    session_id = latest_jsonl
                    try:
                        with open(latest_jsonl) as f:
                            for line in f:
                                try:
                                    data = json.loads(line)
                                    if data.get('type') == 'session':
                                        if not path:
                                            path = data.get('cwd', '')
                                    elif data.get('type') == 'message':
                                        msg = data.get('message', {})
                                        if msg.get('role') == 'user':
                                            content = msg.get('content', [])
                                            if isinstance(content, list):
                                                for block in content:
                                                    if block.get('type') == 'text':
                                                        text = block.get('text', '')
                                                        if text:
                                                            text = re.sub(r'<[^>]+>', '', text).strip()
                                                            text = ' '.join(text.split())
                                                            title = text[:50] + ('...' if len(text) > 50 else '')
                                                            break
                                            elif isinstance(content, str):
                                                text = content
                                                if text:
                                                    text = re.sub(r'<[^>]+>', '', text).strip()
                                                    text = ' '.join(text.split())
                                                    title = text[:50] + ('...' if len(text) > 50 else '')
                                            if title:
                                                break
                                except:
                                    pass
                                if title:
                                    break
                    except:
                        pass
                else:
                    mtime = get_mtime(dp)
                
                if not path:
                    path = '/' + inner.replace('-', '/')
                
                if mtime > 0:
                    sessions.append(Session(timestamp=mtime, agent='Pi CLI', path=path, title=title, session_id=session_id, tool_name='pi'))
    except Exception:
        pass
    return sessions
    try:
        for d in os.listdir(base_dir):
            if d.startswith('--') and d.endswith('--'):
                inner = d[2:-2]
                if inner.startswith('-'):
                    inner = inner[1:]
                
                # Check if we can find the real path in the jsonl
                dp = os.path.join(base_dir, d)
                jsonl_files = glob.glob(os.path.join(dp, '*.jsonl'))
                
                path = ''
                session_id = ''
                if jsonl_files:
                    latest_jsonl = max(jsonl_files, key=get_mtime)
                    mtime = get_mtime(latest_jsonl)
                    session_id = latest_jsonl
                    # For pi, let's see if cwd is in the jsonl
                    try:
                        with open(latest_jsonl) as f:
                            for _ in range(20):
                                line = f.readline()
                                if not line:
                                    break
                                if '"cwd":"' in line or '"workspace":"' in line:
                                    # Fallback basic regex for "cwd" or "workspace"
                                    match = re.search(r'"(?:cwd|workspace)":"([^"]+)"', line)
                                    if match:
                                        path = match.group(1).replace('\\\\', '\\')
                                        break
                    except:
                        pass
                else:
                    mtime = get_mtime(dp)
                
                if not path:
                    # fallback to simple replacement
                    path = '/' + inner.replace('-', '/')
                
                if mtime > 0:
                    sessions.append(Session(timestamp=mtime, agent='Pi CLI', path=path, title='', session_id=session_id, tool_name='pi'))
    except Exception:
        pass
    return sessions

def parse_freebuff():
    sessions = []
    base_dir = os.path.expanduser('~/.config/manicode/projects/')
    if not os.path.exists(base_dir):
        return sessions
    try:
        for project in os.listdir(base_dir):
            project_path = os.path.join(base_dir, project)
            if not os.path.isdir(project_path):
                continue
            chats_dir = os.path.join(project_path, 'chats')
            if not os.path.exists(chats_dir):
                continue
            for session_dir in os.listdir(chats_dir):
                session_path = os.path.join(chats_dir, session_dir)
                if not os.path.isdir(session_path):
                    continue
                # Session ID is the folder name (ISO timestamp)
                session_id = session_dir
                # Get mtime from log.jsonl or run-state.json
                log_path = os.path.join(session_path, 'log.jsonl')
                state_path = os.path.join(session_path, 'run-state.json')
                mtime = 0
                title = ''
                path = project_path
                if log_path and os.path.exists(log_path):
                    mtime = get_mtime(log_path)
                if state_path and os.path.exists(state_path):
                    try:
                        with open(state_path) as f:
                            state = json.load(f)
                            ss = state.get('sessionState', {})
                            mas = ss.get('mainAgentState', {})
                            # Try to get cwd from various places
                            if 'cwd' in mas:
                                path = mas['cwd']
                            elif 'initialCwd' in mas:
                                path = mas['initialCwd']
                            # Try to get first user message as title from messageHistory
                            msg_history = mas.get('messageHistory', [])
                            for msg in msg_history:
                                if msg.get('role') == 'user':
                                    content = msg.get('content', [])
                                    if isinstance(content, list):
                                        for block in content:
                                            if block.get('type') == 'text':
                                                text = block.get('text', '')
                                                if text:
                                                    text = re.sub(r'<[^>]+>', '', text).strip()
                                                    text = ' '.join(text.split())
                                                    title = text[:50] + ('...' if len(text) > 50 else '')
                                                    break
                                    elif isinstance(content, str):
                                        text = content
                                        if text:
                                            text = re.sub(r'<[^>]+>', '', text).strip()
                                            text = ' '.join(text.split())
                                            title = text[:50] + ('...' if len(text) > 50 else '')
                                    if title:
                                        break
                    except:
                        pass
                if mtime > 0:
                    sessions.append(Session(timestamp=mtime, agent='Freebuff', path=path, title=title, session_id=session_id, tool_name='freebuff'))
    except Exception:
        pass
    return sessions
