#!/bin/bash
# 机器人知识库 — Mac 一键启动
# 用法: ./start.sh

set -e

cd "$(dirname "$0")"

# 1. 检查 Python
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
    echo "❌ 需要 Python 3.9+"
    exit 1
fi

echo "📚 机器人知识库 — 启动中..."
echo "   路径: $(pwd)"

# 2. 检查/创建虚拟环境
if [ ! -d "venv" ]; then
    echo "   ▸ 创建虚拟环境..."
    $PYTHON -m venv venv
fi
source venv/bin/activate

# 3. 安装依赖
if [ ! -f "venv/.installed" ]; then
    echo "   ▸ 安装依赖..."
    pip install -q -r requirements.txt 2>&1 | tail -1
    touch venv/.installed
fi

# 4. 导入演示文档（如果数据库为空）
DB="data/robot_knowledge.db"
if [ ! -f "$DB" ] || [ "$($PYTHON -c "import sqlite3; c=sqlite3.connect('$DB'); print(c.execute('SELECT COUNT(*) FROM documents').fetchone()[0]); c.close()" 2>/dev/null || echo 0)" -eq 0 ]; then
    echo "   ▸ 从 wiki/ 导入文档..."
    $PYTHON scripts/import_docs.py wiki/ 2>&1 | tail -3
fi

# 5. 启动服务
echo ""
# 5. 尝试连接 Obsidian
echo ""
echo "   ▸ 检测 Obsidian Local REST API..."
$PYTHON -c "
import sys; sys.path.insert(0, 'backend')
from obsidian_bridge import is_available, configure
if is_available():
    print('   ✅ Obsidian 已连接（可同步笔记到知识库）')
else:
    print('   ⚠️  Obsidian 未连接（安装: obsidian-local-rest-api 插件）')
    print('       不影响核心功能，仅 Obsidian 同步不可用')
" 2>&1

# 6. 启动服务
echo ""
echo "🌐 机器人知识库 — 已就绪"
echo "   ┌──────────────────────────────────────────────┐"
echo "   │  搜索:     http://localhost:8080              │"
echo "   │  图谱:     http://localhost:8080/graph.html   │"
echo "   │  上传:     http://localhost:8080/upload.html  │"
echo "   │  冲突管理: http://localhost:8080/conflicts.html │"
echo "   └──────────────────────────────────────────────┘"
echo ""
echo "   Obsidian 同步（需安装插件）:"
echo "     设置 → 社区插件 → obsidian-local-rest-api → 启用"
echo "     知识库启动后访问 /api/obsidian/status 确认连接"
echo ""
echo "   按 Ctrl+C 停止"
echo ""

cd backend && $PYTHON server.py
