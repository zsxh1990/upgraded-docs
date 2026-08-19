# upgraded-docs MCP Server

本地知识库 MCP 服务器，让 Claude Code、Cursor 等 AI 工具直接搜索 FANUC 机器人知识库。

## 功能

| 工具 | 说明 |
|------|------|
| `upgradeddocs_search` | 语义搜索知识库 |
| `upgradeddocs_get_document` | 获取文档详情 |
| `upgradeddocs_list_categories` | 列出所有分类 |
| `upgradeddocs_get_stats` | 获取统计信息 |

## 安装

### Claude Code

在 `~/.claude/settings.json` 中添加：

```json
{
  "mcpServers": {
    "upgraded-docs": {
      "command": "python3",
      "args": ["/Users/ericjia/repos/upgraded-docs/scripts/mcp_server.py"]
    }
  }
}
```

### Cursor

在 `.cursor/mcp.json` 中添加：

```json
{
  "mcpServers": {
    "upgraded-docs": {
      "command": "python3",
      "args": ["/Users/ericjia/repos/upgraded-docs/scripts/mcp_server.py"]
    }
  }
}
```

## 使用

### 搜索知识库

```
搜索"电池更换"相关文档
```

Claude Code 会自动调用 `upgradeddocs_search` 工具。

### 获取文档详情

```
获取文档 a3e2834964a4ce07 的完整内容
```

### 查看分类

```
列出知识库的所有分类
```

## 技术细节

- **搜索算法**: BM25 + 向量语义搜索（需安装 fastembed）
- **数据库**: SQLite (`data/robot_knowledge.db`)
- **文档数量**: 6649 篇
- **分类数量**: 23 个

## 依赖

```bash
pip install flask fastembed  # 可选，用于向量搜索
```

## 测试

```bash
cd /Users/ericjia/repos/upgraded-docs
/tmp/test_mcp.sh
```

## 故障排除

### 搜索无结果

1. 检查数据库是否存在：`ls -la data/robot_knowledge.db`
2. 重建索引：重启 MCP 服务器（自动重建）

### 导入错误

确保在项目根目录运行：
```bash
cd /Users/ericjia/repos/upgraded-docs
python3 scripts/mcp_server.py
```
