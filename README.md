# 机器人/仿真标准规范知识库

轻量级本地知识库系统，面向工业自动化领域，支持语义搜索、冲突管理、知识图谱可视化和 Obsidian 集成。

## 功能特性

- **语义搜索** — BM25 关键词 + 向量语义 双通道混合检索（RRF 融合），支持同义词扩展和实体精确匹配
- **冲突管理** — 文档导入时自动检测语义冲突（相似度 > 0.82），支持人工审核、提方案、定版解决的全生命周期管理
- **知识图谱** — D3.js 力导向图可视化，支持节点筛选、子图钻取、类型过滤
- **结构化参数检索** — Excel 表格解析，同名参数跨文档数值比对
- **Obsidian 双向同步** — 自动索引、Wikilink 解析、回写标注
- **多格式导入** — 支持 .md .txt .docx .xlsx .pptx .pdf
- **纯内网部署** — 零外网依赖，端口绑定 127.0.0.1

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python Flask 3.x |
| 数据库 | SQLite |
| 向量引擎 | fastembed (bge-small-zh-v1.5, ONNX) 或 LM Studio |
| 搜索算法 | BM25 + 向量余弦 + RRF 融合 |
| 前端 | 原生 HTML/CSS/JS |
| 可视化 | D3.js |
| 部署 | Docker / systemd / nohup |

## 快速开始

### 一键启动（推荐）

```bash
./start.sh
```

脚本会自动：
1. 创建 Python 虚拟环境
2. 安装依赖
3. 导入 `wiki/` 目录中的示例文档
4. 检测 Obsidian 插件
5. 启动 Web 服务

### Docker 部署

```bash
docker compose up -d
```

### 手动启动

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 backend/server.py
```

## 访问

启动后打开浏览器访问：

| 页面 | 地址 |
|------|------|
| 搜索首页 | http://localhost:5000 |
| 冲突管理 | http://localhost:5000/conflicts.html |
| 知识图谱 | http://localhost:5000/graph.html |
| 文档上传 | http://localhost:5000/upload.html |

## API 概览

所有 API 以 `/api/` 开头：

| 端点 | 说明 |
|------|------|
| `GET /api/search?q=&category=&top_k=` | 语义搜索 |
| `GET /api/search/suggest?q=` | 搜索建议（≥2字符） |
| `GET /api/documents` | 文档列表 |
| `GET /api/document/<id>` | 文档详情 |
| `GET /api/stats` | 统计（文档数/冲突数/配置数） |
| `GET /api/search/mode` | 搜索模式（vector/bm25_only） |
| `GET/POST /api/conflicts` | 冲突列表/新建 |
| `POST /api/conflicts/<id>/resolve` | 解决冲突 |
| `POST /api/upload` | 上传文档 |
| `GET /api/graph` | 知识图谱数据 |
| `GET /api/obsidian/status` | Obsidian 连接状态 |
| `GET /api/parameters` | 结构化参数查询 |

## 项目结构

```
upgraded-docs/
├── backend/             # Flask 后端
│   ├── server.py        # API 路由与启动
│   ├── search_engine.py # 语义搜索引擎
│   ├── conflict_detector.py  # 冲突检测
│   ├── obsidian_bridge.py    # Obsidian 集成
├── frontend/            # 前端页面
│   ├── index.html       # 搜索首页
│   ├── document.html    # 文档详情
│   ├── conflicts.html   # 冲突管理
│   ├── graph.html       # 知识图谱
│   ├── upload.html      # 文档上传
├── scripts/             # 工具脚本
│   ├── import_docs.py   # 文档批量导入
│   ├── hyperlink_fetcher.py # 超链接拉取
│   ├── cross_validate.py    # 交叉验证
│   ├── merge_knowledge_graph.py # 图谱合并
│   ├── analyze_knowledge.py     # 知识库分析
│   └── ...
├── data/                # 数据目录
│   ├── robot_knowledge.db  # SQLite 数据库
│   ├── documents/       # 导入的文档
│   ├── conflicts/       # 冲突 YAML 记录
│   └── synonyms.json    # 同义词表
├── wiki/                # Obsidian Wiki 仓库
├── docker-compose.yml   # Docker 部署
├── Dockerfile
├── requirements.txt
└── start.sh             # 一键启动脚本
```

## Obsidian 集成

需要安装 [obsidian-local-rest-api](https://github.com/coddingtonbear/obsidian-local-rest-api) 插件。安装后启动 Obsidian，系统会自动检测并启用双向同步功能。

## 配置

- 端口绑定：`127.0.0.1:5000`（环境变量 `KB_HOST` 可修改）
- LM Studio：设置环境变量 `LM_STUDIO_URL=http://localhost:1234/v1`
- 上传限制：50MB
- 搜索模式自动降级：向量不可用时回退 BM25