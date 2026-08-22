#!/bin/bash
# One-time engine switch: vanilla -> Paper 26.2 (same world, same IP, plugins unlocked).
# Run:  bash ~/panel/migrate-to-paper.sh     (server goes down ~2-3 minutes)
set -e
MC=/home/ubuntu/minecraft
echo "== 1/6 Snapshot backup before anything =="
bash $MC/backup.sh || true
sleep 12
echo "== 2/6 Finding newest Paper build for 26.2 =="
INFO=$(python3 - <<'EOF'
import json, urllib.request
v = "26.2"
b = json.load(urllib.request.urlopen(f"https://api.papermc.io/v2/projects/paper/versions/{v}/builds"))["builds"][-1]
n = b["build"]; f = b["downloads"]["application"]["name"]; sha = b["downloads"]["application"]["sha256"]
print(f"https://api.papermc.io/v2/projects/paper/versions/{v}/builds/{n}/downloads/{f}", f, sha)
EOF
)
URL=$(echo $INFO | cut -d' ' -f1); JAR=$(echo $INFO | cut -d' ' -f2); SHA=$(echo $INFO | cut -d' ' -f3)
echo "   $JAR"
echo "== 3/6 Downloading =="
wget -q -O "$MC/$JAR" "$URL"
echo "$SHA  $MC/$JAR" | sha256sum -c - || { echo "CHECKSUM MISMATCH — aborting, nothing changed"; rm -f "$MC/$JAR"; exit 1; }
echo "== 4/6 Stopping server and switching the engine =="
sudo systemctl stop minecraft
sudo sed -i "s|-jar /home/ubuntu/minecraft/server.jar|-jar /home/ubuntu/minecraft/$JAR|; s|-jar server.jar|-jar $JAR|" /etc/systemd/system/minecraft.service
sudo systemctl daemon-reload
mkdir -p $MC/plugins
touch $MC/.paper-engine
echo "== 5/6 Starting Paper (first boot takes ~1 min) =="
sudo systemctl start minecraft
sleep 45
echo "== 6/6 Tail of the log =="
tail -n 12 $MC/logs/latest.log
echo
echo "If you see Done (...s)! above — Paper is live. Plugins can now be uploaded from the panel."
echo "To go back to vanilla ever: reverse the ExecStart line to server.jar and restart."
