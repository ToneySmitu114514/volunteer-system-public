#!/bin/bash
# 一键部署脚本：适用于 Debian / Ubuntu
# 在服务器项目目录执行：bash deploy.sh
set -e

NGINX_CONF="/etc/nginx/conf.d/volunteer.conf"
SVC_FILE="/etc/systemd/system/volunteer.service"

echo "[1/4] 安装 python3 与 nginx ..."
apt update
apt install -y python3 nginx

echo "[2/4] 配置 Nginx 反向代理，80 转到本机 8088 ..."
cat > "$NGINX_CONF" <<'EOF'
server {
    listen 80;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF
nginx -t
systemctl reload nginx 2>/dev/null || systemctl start nginx

echo "[3/4] 注册开机自启服务 ..."
cat > "$SVC_FILE" <<'EOF'
[Unit]
Description=Fuzhou No.3 Volunteer System
After=network.target

[Service]
WorkingDirectory=/root/volunteer-system-py
ExecStart=/usr/bin/python3 server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable volunteer

echo "[4/4] 启动服务 ..."
systemctl restart volunteer
sleep 2
systemctl --no-pager --full status volunteer | head -n 12

echo ""
echo "部署完成。外网访问地址: http://8.138.191.20:35002"
echo "登录后请立刻在页面右上角修改口令。"
