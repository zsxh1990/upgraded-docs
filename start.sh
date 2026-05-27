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
# 5. 检测 Obsidian
echo ""
echo "   ▸ 检测 Obsidian..."
$PYTHON -c "
import sys; sys.path.insert(0, 'backend')
from obsidian_bridge import status, list_notes
s = status()
if s['local_mode']:
    notes = list_notes()
    print(f'   ✅ Obsidian vault 已发现: {s[\"vault_dir\"]}')
    print(f'      笔记数: {len(notes)}')
    if s['rest_api']:
        print('      REST API: 可用（支持回写标注）')
    else:
        print('      回写标注: 需安装 obsidian-local-rest-api 插件')
elif s['rest_api']:
    print('   ✅ Obsidian REST API 已连接')
else:
    print('   ⚠️  Obsidian 未找到（不影响核心搜索功能）')
    print('       提示: 设置 OBSIDIAN_VAULT 环境变量指定 vault 路径')
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
echo "     如需 API Key: export OBSIDIAN_API_KEY=你的密钥"
echo ""
echo "   按 Ctrl+C 停止"
echo ""

cd backend && $PYTHON server.py
