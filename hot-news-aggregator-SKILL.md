---
name: hot-news-aggregator
description: 生成可配置的热点聚合网站，支持30+数据源、智能评分、8维AI价值标签、全量AI评论+行动引导、原出处标注、暗色模式、邮件订阅、多平台部署。当用户说热点网站、热点聚合、news aggregator、生成网站时触发。
disable: false
---

# 通用热点聚合网站生成器 v3.9

生成可配置的热点聚合网站，支持30+数据源（含博主源+价值标签驱动源）、智能评分（含可靠性加权+价值标签关键词群）、8维AI价值标签、原出处标注、AI视角评论+行动引导（全量覆盖）、邮件订阅、多源去重。

## 项目结构

```
hot-news-site/
├── generate_v3.7.py         # 主生成器（含全流程进度条）
├── ops_tool.py              # 运维自检工具
├── config.yaml              # 配置文件
├── ai_content.json          # AI评论内容存储（JSON格式，全量32条）
├── inject_ai_from_json.py   # AI内容注入脚本
├── deploy.py                # 多平台部署管理
├── github_deploy.py         # GitHub部署工具
└── site/hot-news.html       # 生成的网站
```

## 触发方式

用户说"帮我生成热点网站"、"创建热点聚合"、"生成 news aggregator"、"热点网站"时触发。

## 执行流程

### 第一步：确认最终产物类型

两种产物类型：

| | 产物A：在线网页 | 产物B：本地文件 |
|---|---|---|
| 部署 | Git 推送 → 平台自动部署 | 生成 HTML 到本地目录 |
| 外部链接 | ✅ 点击跳转 | ❌ file:// 限制 |
| 翻译 | ✅ 可用 | ❌ 浏览器禁止 |
| 适用 | 长期运营、多人访问 | 一次性分享、内网环境 |

本地 HTML 须提醒：`file://` 协议限制，翻译和外部链接跳转失效。在线网页支持 staging 预览（`safety_settings.preview_mode`）。

### 第二步：确认安全设置（强制，不可跳过）

向用户展示 `config.yaml` 的 `safety_settings`：

| 设置项 | 推荐默认 | 说明 |
|---|---|---|
| 翻译限频 `rate_limit` | ON (2w/0.5s) | 防止 API 封禁 |
| 预览确认 `preview_mode` | ON（在线）/ OFF（本地） | 防止错误直接上线 |
| 失败重试 `retry` | ON (2x) | 网络波动容错 |
| 严格模式 `strict_mode` | OFF | OFF=跳过失败源继续 |
| 超时保护 `timeout` | ON | 防止慢源卡死 |

用户必须明确回答「使用默认」或「我要修改 XX」才能继续。

### 第三步：确认用户定位

引导确认网站标题、副标题、关注领域（AI/科技/金融/设计等）、描述信息。已有 config.yaml 可复用。

### 第四步：确认数据源

按用户定位推荐数据源组合。完整数据源清单（30+个）见 [docs/datasource-reference.md](docs/datasource-reference.md)。

核心分类：英文科技源（HN/GitHub/TechCrunch 等，默认开启）、中文科技源（36氪/机器之心/量子位等，默认开启）、中文综合源（澎湃/V2EX/微博等，按需开启）、AI热点源（卡兹克）、博主源（阮一峰等4位）、价值标签驱动源（arXiv/HuggingFace 等8个，默认关闭按需开启）。

死源已关闭：Product Hunt、ZDNet、日经亚洲、极客公园、品玩、cnBeta。

### 第五步：确认评分模式

- 三档制：精选(80+) / 推荐(70+) / 参考(60+)
- 四档制：S(90+) / A(80+) / B(70+) / C(60+)
- 两档制：必读(75+) / 其他
- 自定义：用户指定档位、名称、阈值、颜色

### 第六步：确认 AI 评论

- 不启用：仅原文+中文小结
- Skill 模式（推荐）：AI 对话中手工生成，存 `ai_content.json`，通过 `inject_ai_from_json.py` 注入
- API 模式：调用 LLM API 自动生成
- 双模式：两者并存

全量覆盖：为所有展示卡片（~32条）生成三层 AI 内容（📝小结 + 💡评论 + 🎯行动引导）。

### 第七步：确认其他功能

邮件订阅（SMTP）、主题风格、翻译、动画效果。

### 第八步：准备项目

依赖安装：`pip install httpx pyyaml deep-translator`

### 第九步：生成 config.yaml

根据前几步确认生成。

### 第十步：生成并验证

```bash
python generate_v3.7.py -m web        # 标准模式（~30-90秒）
python generate_v3.7.py --quick       # 快速模式（~8-15秒，跳过翻译+AI评论）
python generate_v3.7.py --no-translate # 跳过翻译
python generate_v3.7.py --update-only  # 仅更新内容
python generate_v3.7.py -m local       # 本地HTML
```

| 参数 | 说明 | 默认 |
|------|------|------|
| -c/--config | 配置文件路径 | config.yaml |
| -m/--mode | web/local | web |
| --quick | 快速模式 | false |
| --preview | 生成staging文件 | false |
| --confirm | 确认发布 | false |

**推送前自检（强制）：**

```bash
python ops_tool.py safety   # 安全设置检查
python ops_tool.py check    # 内容完整性检查（必须通过才能 push）
python ops_tool.py diagnose # 全量诊断（可选）
```

验证清单：主题颜色、档位标签、卡片内容（标题/摘要/小结/评论/引导）、在线跳转/翻译、搜索/收藏/暗色、移动端响应式。

### 第十一步：运维自检

```bash
python ops_tool.py quick     # 综合诊断（推荐，~15秒）
python ops_tool.py fix       # 自动修复
python ops_tool.py history   # 历史分析
```

### 第十二步：交付与部署

通过 `deploy.py` 统一管理多平台：

| 平台 | 命令 | 说明 |
|------|------|------|
| EdgeOne Pages（首选） | `python deploy.py edgeone` | 国内秒开，支持 git push 自动部署 |
| GitHub Pages | `python deploy.py github` | `https://afuxh.github.io/fuqing-profile/hot-news.html` |
| Cloudflare Pages | `python deploy.py cloudflare` | 兜底 |
| 全平台 | `python deploy.py all` | 同时部署所有 |
| 查看状态 | `python deploy.py status` | 各平台状态 |

在线网页预览确认流程：`--preview` 生成 staging → 查看确认 → `--preview --confirm` 发布 → `ops_tool.py check` 验证 → git push。

## 核心机制速览

- **多源去重**：标题归一化完全匹配 + 编辑距离模糊匹配（阈值0.8）+ 合并显示多来源标签
- **原出处标注**：卡兹克源通过 `_extract_domain_source()` 解析50+域名映射，博主源自动标注
- **博主可靠性**：4位博主附可靠性评分（1-5星）+ 偏差提示 + 可信度徽章
- **评分系统**：核心关键词+15/标题命中+8、扩展+8、排除-15、热度0-35分、博主源加权2-8分
- **邮件订阅**：S/A级定时推送、SMTP配置、自动+手动双模式

## 快速模式（8-15秒）

用户只说领域，其余全走默认：`python generate_v3.7.py --quick`。跳过翻译和AI评论，只执行抓取→评分→渲染。

## 深入文档

| 文档 | 内容 |
|------|------|
| [数据源完整参考](docs/datasource-reference.md) | 30+数据源清单、字段、稳定性、8维价值标签系统 |
| [故障排除](docs/troubleshooting.md) | 5个实战问题及解决方案（Actions崩溃/f-string/Git认证等） |
| [扩展指南](docs/extension-guide.md) | 添加新数据源、自定义AI提示词、评分算法、邮件模板 |
| [版本历史](docs/version-history.md) | v3.3-v3.9 变更记录 |
| [验证测试记录](docs/verification-records.md) | 历次端到端验证结果 |
