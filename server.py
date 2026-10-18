import json
import os
import socket
import sqlite3
import datetime
import hashlib
import hmac
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "app.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")
HOST = "0.0.0.0"
PORT = 8088
MAX_BODY = 5 * 1024 * 1024  # 请求体上限 5MB

RULES = ("equal", "full")  # 分配规则 平均或全员计满

DEFAULT_PASSWORD = "985211"  # 初始默认口令
APP_PASSWORD = os.environ.get("APP_PASSWORD", DEFAULT_PASSWORD)
PWD_FILE = os.path.join(BASE_DIR, "password.dat")  # 口令哈希文件
PWD_ITER = 200000  # 哈希迭代次数
PWD_RECORD = None  # 有值表示已自定义口令
AUTH_TOKEN = "fz3z-vol-2026"  # 登录令牌


# 取当前时间
def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 打开数据库连接
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


# 读取已保存的口令哈希
def load_pwd():
    global PWD_RECORD
    PWD_RECORD = None
    try:
        with open(PWD_FILE, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            iters, salt, digest = text.split("$")
            PWD_RECORD = {"iter": int(iters), "salt": salt, "hash": digest}
    except (FileNotFoundError, ValueError):
        PWD_RECORD = None


# 用盐和迭代生成口令哈希
def hash_pwd(pwd, salt_hex, iters):
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt, iters)
    return digest.hex()


# 校验输入口令是否正确
def check_pwd(pwd):
    if PWD_RECORD is None:
        return hmac.compare_digest(pwd, APP_PASSWORD)
    got = hash_pwd(pwd, PWD_RECORD["salt"], PWD_RECORD["iter"])
    return hmac.compare_digest(got, PWD_RECORD["hash"])


# 保存新口令哈希到文件
def save_pwd(pwd):
    global PWD_RECORD
    salt = os.urandom(16).hex()
    iters = PWD_ITER
    digest = hash_pwd(pwd, salt, iters)
    with open(PWD_FILE, "w", encoding="utf-8") as f:
        f.write("%d$%s$%s" % (iters, salt, digest))
    PWD_RECORD = {"iter": iters, "salt": salt, "hash": digest}


SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    student_id  TEXT DEFAULT '',
    note        TEXT DEFAULT '',
    created_at  TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    content     TEXT DEFAULT '',
    task_date   TEXT DEFAULT '',
    start_time  TEXT DEFAULT '',
    end_time    TEXT DEFAULT '',
    total_hours REAL DEFAULT 0,
    rule        TEXT DEFAULT 'equal',
    status      TEXT DEFAULT '已完成',
    location    TEXT DEFAULT '',
    created_at  TEXT DEFAULT '',
    updated_at  TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS task_members (
    task_id   INTEGER,
    member_id INTEGER,
    PRIMARY KEY (task_id, member_id),
    FOREIGN KEY (task_id)   REFERENCES tasks(id)   ON DELETE CASCADE,
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE
);
"""


# 初始化数据表
def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript(SCHEMA)
    conn.commit()
    conn.close()


# 统计各成员志愿时长
def sum_hours(conn):
    c = conn.cursor()
    members = c.execute("SELECT id, name FROM members ORDER BY id").fetchall()
    out = {}
    for m in members:
        out[m["id"]] = {
            "id": m["id"],
            "name": m["name"],
            "total": 0.0,
            "count": 0,
            "details": [],
        }
    tasks = c.execute(
        "SELECT id, title, task_date, total_hours, rule FROM tasks ORDER BY task_date"
    ).fetchall()

    for t in tasks:
        tid = t["id"]
        title = t["title"]
        date = t["task_date"]
        hours = float(t["total_hours"] or 0)
        rule = t["rule"]
        joins = c.execute(
            "SELECT member_id FROM task_members WHERE task_id=?", (tid,)
        ).fetchall()
        num = len(joins)
        if num == 0:
            continue
        each = hours if rule == "full" else (hours / num)
        each = round(each, 2)
        for p in joins:
            mid = p["member_id"]
            if mid in out:
                out[mid]["total"] += each
                out[mid]["count"] += 1
                out[mid]["details"].append(
                    {
                        "task_id": tid,
                        "title": title,
                        "date": date,
                        "hours": each,
                        "rule": rule,
                    }
                )

    for mid in out:
        out[mid]["total"] = round(out[mid]["total"], 2)
    return out


# 组装任务详情数据
def pack_task(conn, row):
    c = conn.cursor()
    tid = row["id"]
    members = c.execute(
        """SELECT m.id, m.name FROM task_members tm
           JOIN members m ON m.id = tm.member_id
           WHERE tm.task_id=? ORDER BY m.id""",
        (tid,),
    ).fetchall()
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "task_date": row["task_date"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "total_hours": row["total_hours"],
        "rule": row["rule"],
        "status": row["status"],
        "location": row["location"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "participants": [{"id": m["id"], "name": m["name"]} for m in members],
        "participant_count": len(members),
    }


# 重设任务参与成员
def set_members(conn, tid, ids):
    c = conn.cursor()
    c.execute("DELETE FROM task_members WHERE task_id=?", (tid,))
    for mid in ids:
        found = c.execute("SELECT 1 FROM members WHERE id=?", (mid,)).fetchone()
        if found:
            c.execute(
                "INSERT OR IGNORE INTO task_members(task_id, member_id) VALUES (?, ?)",
                (tid, mid),
            )


# 请求处理器
class Handler(BaseHTTPRequestHandler):
    timeout = 20
    protocol_version = "HTTP/1.0"

    # 静默屏蔽访问日志
    def log_message(self, fmt, *args):
        pass

    # 发送 JSON 响应
    def reply(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # 发送静态文件
    def send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            self.reply({"error": "file not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # 读取并缓存请求体
    def read_body(self):
        if hasattr(self, "body_cache"):
            return self.body_cache
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            self.body_cache = {}
            return self.body_cache
        if length > MAX_BODY:
            self.reply({"error": "payload too large"}, 413)
            self.body_cache = None
            return None
        raw = self.rfile.read(length)
        try:
            self.body_cache = json.loads(raw.decode("utf-8"))
        except Exception:
            self.body_cache = {}
        return self.body_cache

    # 按后缀判断文件类型
    def file_type(self, path):
        if path.endswith(".html"):
            return "text/html; charset=utf-8"
        if path.endswith(".css"):
            return "text/css; charset=utf-8"
        if path.endswith(".js"):
            return "application/javascript; charset=utf-8"
        if path.endswith(".svg"):
            return "image/svg+xml"
        if path.endswith(".json"):
            return "application/json; charset=utf-8"
        if path.endswith(".png"):
            return "image/png"
        if path.endswith(".jpg") or path.endswith(".jpeg"):
            return "image/jpeg"
        if path.endswith(".ico"):
            return "image/x-icon"
        if path.endswith(".gif"):
            return "image/gif"
        return "application/octet-stream"

    # 处理 GET 请求
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.send_file(os.path.join(STATIC_DIR, "index.html"),
                           "text/html; charset=utf-8")
            return

        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            rel = rel.replace("\\", "/")
            full = os.path.normpath(os.path.join(STATIC_DIR, rel))
            if not full.startswith(STATIC_DIR):
                self.reply({"error": "forbidden"}, 403)
                return
            self.send_file(full, self.file_type(rel))
            return

        if path.startswith("/api/") and path != "/api/login":
            if not self.check_login():
                self.reply({"error": "未登录"}, 401)
                return
        if path == "/api/stats":
            return self.api_stats()
        if path == "/api/tasks":
            return self.api_task_list()
        if path == "/api/members":
            return self.api_member_list()
        if path.startswith("/api/members/") and path.endswith("/hours"):
            mid = path[len("/api/members/"):-len("/hours")].strip("/")
            return self.api_member_hours(mid)
        if path.startswith("/api/tasks/"):
            tid = path[len("/api/tasks/"):].strip("/")
            return self.api_task_detail(tid)

        self.reply({"error": "not found"}, 404)

    # 处理 POST 请求
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/login":
            return self.api_login()
        if path.startswith("/api/") and not self.check_login():
            self.reply({"error": "未登录"}, 401)
            return
        if self.read_body() is None:
            return
        if path == "/api/password":
            return self.api_password()
        if path == "/api/tasks":
            return self.api_task_create()
        if path == "/api/members":
            return self.api_member_create()
        self.reply({"error": "not found"}, 404)

    # 处理 PUT 请求
    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/") and not self.check_login():
            self.reply({"error": "未登录"}, 401)
            return
        if self.read_body() is None:
            return
        if path.startswith("/api/tasks/"):
            tid = path[len("/api/tasks/"):].strip("/")
            return self.api_task_update(tid)
        if path.startswith("/api/members/"):
            mid = path[len("/api/members/"):].strip("/")
            return self.api_member_update(mid)
        self.reply({"error": "not found"}, 404)

    # 处理 DELETE 请求
    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/") and not self.check_login():
            self.reply({"error": "未登录"}, 401)
            return
        if path.startswith("/api/tasks/"):
            tid = path[len("/api/tasks/"):].strip("/")
            return self.api_task_delete(tid)
        if path.startswith("/api/members/"):
            mid = path[len("/api/members/"):].strip("/")
            return self.api_member_delete(mid)
        self.reply({"error": "not found"}, 404)

    # 任务列表接口
    def api_task_list(self):
        conn = get_conn()
        c = conn.cursor()
        rows = c.execute("SELECT * FROM tasks ORDER BY task_date DESC, id DESC").fetchall()
        out = [pack_task(conn, r) for r in rows]
        conn.close()
        self.reply({"tasks": out})

    # 单个任务详情接口
    def api_task_detail(self, tid):
        try:
            tid = int(tid)
        except ValueError:
            return self.reply({"error": "bad id"}, 400)
        conn = get_conn()
        c = conn.cursor()
        row = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        if not row:
            conn.close()
            return self.reply({"error": "not found"}, 404)
        out = pack_task(conn, row)
        conn.close()
        self.reply(out)

    # 从请求体解析任务
    def parse_task(self, body):
        title = (body.get("title") or "").strip()
        if not title:
            return None, "任务标题不能为空"
        rule = body.get("rule", "equal")
        if rule not in RULES:
            rule = "equal"
        try:
            hours = float(body.get("total_hours") or 0)
        except (TypeError, ValueError):
            hours = 0.0
        if hours < 0:
            hours = 0.0
        ids = [int(x) for x in (body.get("member_ids") or []) if str(x).strip()]
        return {
            "title": title,
            "content": (body.get("content") or "").strip(),
            "task_date": (body.get("task_date") or "").strip(),
            "start_time": (body.get("start_time") or "").strip(),
            "end_time": (body.get("end_time") or "").strip(),
            "total_hours": hours,
            "rule": rule,
            "status": (body.get("status") or "已完成").strip() or "已完成",
            "location": (body.get("location") or "").strip(),
            "member_ids": ids,
        }, None

    # 新建任务接口
    def api_task_create(self):
        body = self.read_body()
        data, err = self.parse_task(body)
        if err:
            return self.reply({"error": err}, 400)
        conn = get_conn()
        c = conn.cursor()
        now = now_iso()
        c.execute(
            """INSERT INTO tasks
               (title, content, task_date, start_time, end_time,
                total_hours, rule, status, location, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (data["title"], data["content"], data["task_date"], data["start_time"],
             data["end_time"], data["total_hours"], data["rule"], data["status"],
             data["location"], now, now),
        )
        tid = c.lastrowid
        set_members(conn, tid, data["member_ids"])
        conn.commit()
        row = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        out = pack_task(conn, row)
        conn.close()
        self.reply(out, 201)

    # 更新任务接口
    def api_task_update(self, tid):
        try:
            tid = int(tid)
        except ValueError:
            return self.reply({"error": "bad id"}, 400)
        body = self.read_body()
        data, err = self.parse_task(body)
        if err:
            return self.reply({"error": err}, 400)
        conn = get_conn()
        c = conn.cursor()
        row = c.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone()
        if not row:
            conn.close()
            return self.reply({"error": "not found"}, 404)
        c.execute(
            """UPDATE tasks SET
                  title=?, content=?, task_date=?, start_time=?, end_time=?,
                  total_hours=?, rule=?, status=?, location=?, updated_at=?
               WHERE id=?""",
            (data["title"], data["content"], data["task_date"], data["start_time"],
             data["end_time"], data["total_hours"], data["rule"], data["status"],
             data["location"], now_iso(), tid),
        )
        set_members(conn, tid, data["member_ids"])
        conn.commit()
        row = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        out = pack_task(conn, row)
        conn.close()
        self.reply(out)

    # 删除任务接口
    def api_task_delete(self, tid):
        try:
            tid = int(tid)
        except ValueError:
            return self.reply({"error": "bad id"}, 400)
        conn = get_conn()
        c = conn.cursor()
        c.execute("DELETE FROM tasks WHERE id=?", (tid,))
        conn.commit()
        conn.close()
        self.reply({"ok": True})

    # 成员列表接口
    def api_member_list(self):
        conn = get_conn()
        c = conn.cursor()
        rows = c.execute("SELECT * FROM members ORDER BY id").fetchall()
        out = [dict(r) for r in rows]
        conn.close()
        self.reply({"members": out})

    # 新建成员接口
    def api_member_create(self):
        body = self.read_body()
        name = (body.get("name") or "").strip()
        if not name:
            return self.reply({"error": "成员姓名不能为空"}, 400)
        conn = get_conn()
        c = conn.cursor()
        found = c.execute("SELECT id FROM members WHERE name=?", (name,)).fetchone()
        if found:
            conn.close()
            return self.reply({"error": "该成员已存在", "id": found["id"]}, 409)
        c.execute(
            "INSERT INTO members(name, student_id, note, created_at) VALUES (?,?,?,?)",
            (name, (body.get("student_id") or "").strip(),
             (body.get("note") or "").strip(), now_iso()),
        )
        mid = c.lastrowid
        conn.commit()
        row = c.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        conn.close()
        self.reply(dict(row), 201)

    # 更新成员接口
    def api_member_update(self, mid):
        try:
            mid = int(mid)
        except ValueError:
            return self.reply({"error": "bad id"}, 400)
        body = self.read_body()
        conn = get_conn()
        c = conn.cursor()
        if not c.execute("SELECT 1 FROM members WHERE id=?", (mid,)).fetchone():
            conn.close()
            return self.reply({"error": "not found"}, 404)
        c.execute(
            "UPDATE members SET name=?, student_id=?, note=? WHERE id=?",
            ((body.get("name") or "").strip(), (body.get("student_id") or "").strip(),
             (body.get("note") or "").strip(), mid),
        )
        conn.commit()
        row = c.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        conn.close()
        self.reply(dict(row))

    # 删除成员接口
    def api_member_delete(self, mid):
        try:
            mid = int(mid)
        except ValueError:
            return self.reply({"error": "bad id"}, 400)
        conn = get_conn()
        c = conn.cursor()
        c.execute("DELETE FROM members WHERE id=?", (mid,))
        conn.commit()
        conn.close()
        self.reply({"ok": True})

    # 成员时长明细接口
    def api_member_hours(self, mid):
        try:
            mid = int(mid)
        except ValueError:
            return self.reply({"error": "bad id"}, 400)
        conn = get_conn()
        c = conn.cursor()
        m = c.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        if not m:
            conn.close()
            return self.reply({"error": "not found"}, 404)
        table = sum_hours(conn)
        info = table.get(mid, {"id": mid, "name": m["name"], "total": 0, "count": 0, "details": []})
        conn.close()
        self.reply(info)

    # 统计数据接口
    def api_stats(self):
        conn = get_conn()
        c = conn.cursor()
        tasks = c.execute("SELECT * FROM tasks").fetchall()
        members = c.execute("SELECT * FROM members").fetchall()

        task_count = len(tasks)
        recorded_hours = round(sum(float(t["total_hours"] or 0) for t in tasks), 2)
        member_count = len(members)

        month = datetime.datetime.now().strftime("%Y-%m")
        month_tasks = sum(1 for t in tasks if (t["task_date"] or "").startswith(month))

        table = sum_hours(conn)
        rank = sorted(table.values(), key=lambda x: x["total"], reverse=True)
        total = round(sum(x["total"] for x in rank), 2)

        month_map = {}
        for t in tasks:
            key = (t["task_date"] or "")[:7]
            if key:
                month_map[key] = round(month_map.get(key, 0) + float(t["total_hours"] or 0), 2)
        timeline = [{"month": k, "hours": v} for k, v in sorted(month_map.items())]

        recent = [pack_task(conn, r) for r in
                  c.execute("SELECT * FROM tasks ORDER BY task_date DESC, id DESC LIMIT 5").fetchall()]

        conn.close()
        self.reply({
            "totals": {
                "task_count": task_count,
                "recorded_hours": recorded_hours,
                "allocated_total": total,
                "member_count": member_count,
                "month_tasks": month_tasks,
            },
            "per_member": rank,
            "timeline": timeline,
            "recent": recent,
        })

    # 校验登录令牌
    def check_login(self):
        if self.headers.get("X-Auth-Token") == AUTH_TOKEN:
            return True
        auth = self.headers.get("Authorization") or ""
        return auth.startswith("Bearer ") and auth[7:] == AUTH_TOKEN

    # 登录接口
    def api_login(self):
        body = self.read_body()
        if body is None:
            return
        pwd = (body.get("password") or "").strip()
        if check_pwd(pwd):
            self.reply({"ok": True, "token": AUTH_TOKEN})
        else:
            self.reply({"error": "口令错误"}, 401)

    # 修改访问口令接口
    def api_password(self):
        body = self.read_body()
        if body is None:
            return
        old = (body.get("old") or "")
        new = (body.get("new") or "")
        if not check_pwd(old):
            return self.reply({"error": "旧口令错误"}, 400)
        if len(new) < 6:
            return self.reply({"error": "新口令至少 6 位"}, 400)
        if new == old:
            return self.reply({"error": "新口令不能与旧口令相同"}, 400)
        save_pwd(new)
        self.reply({"ok": True})


# 多线程 HTTP 服务
class ThreadedServer(ThreadingHTTPServer):

    daemon_threads = True
    allow_reuse_address = True

    # 绑定端口跳过反查
    def server_bind(self):
        import socketserver
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

    # 忽略浏览器断连错误
    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        ThreadingHTTPServer.handle_error(self, request, client_address)


# IPv4 与 IPv6 双栈
class ThreadedServerDual(ThreadedServer):
    address_family = socket.AF_INET6

    # 开启双栈再绑定
    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        ThreadedServer.server_bind(self)


# 选择服务器并找端口
def make_server():
    for factory, addr, desc in (
        (ThreadedServerDual, "::", "IPv4 加 IPv6 双栈"),
        (ThreadedServer, "0.0.0.0", "仅 IPv4"),
    ):
        for p in range(PORT, PORT + 100):
            try:
                return factory((addr, p), Handler), p, desc
            except OSError:
                continue
    return None, PORT, ""


# 程序入口
def main():
    init_db()
    load_pwd()
    server, used_port, desc = make_server()
    if server is None:
        print("错误：无法在 %d~%d 范围内找到可用端口。" % (PORT, PORT + 99))
        return
    print("=" * 56)
    print("  福州三中志愿登记系统 已启动")
    print("  访问地址: http://127.0.0.1:%d" % used_port)
    print("            http://localhost:%d" % used_port)
    print("  监听方式: %s" % desc)
    print("  数据库文件: %s" % DB_PATH)
    if PWD_RECORD is not None:
        print("  访问口令: 已自定义（存于口令哈希文件）")
    else:
        print("  访问口令: %s （可用环境变量覆盖）" % APP_PASSWORD)
    print("  按 Ctrl+C 停止服务")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
        server.server_close()


if __name__ == "__main__":
    main()
