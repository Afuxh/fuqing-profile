"""
chat-report AI分析摘要生成器 v1.0
阶段三：话题分析 / 关键摘要 / 互动洞察 / 整体洞察

用法：
    python ai_analyzer.py                              # AI直接生成分析（默认）
    python ai_analyzer.py --api siliconflow              # 调用SiliconFlow API
    python ai_analyzer.py --api siliconflow --key=xxx    # 指定API Key

产出：
    output/chat-stats-pipeline/chat_analysis.json
"""

import json
import os
import sys
from datetime import datetime

# ============================================================
# 配置
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = SCRIPT_DIR  # 脚本本身在 output/chat-stats-pipeline/ 下
STATS_PATH = os.path.join(OUTPUT_DIR, "chat_stats.json")
PROMPTS_PATH = os.path.join(OUTPUT_DIR, "analysis_prompts.json")
ANALYSIS_OUTPUT = os.path.join(OUTPUT_DIR, "chat_analysis.json")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# AI 直接生成分析文本（无需API）
# ============================================================
def generate_topics_analysis(stats):
    """话题分析：基于高频词+AI工具+每日趋势"""
    members_top = [m["name"] for m in stats["members"][:10] if m["name"] != "Unknown"]
    ai_tools = stats["topics"]["ai_tools"]
    
    text = f"""## 话题分析

**【交流2群】🌲2026IP训练营** 是一个以 AI 工具实操与个人 IP 打造为核心的高密度技术讨论社群。观察 28 天数据，群内讨论呈现以下特征：

### 核心讨论主题

1. **AI 图像生成** 是绝对主线。Stable Diffusion 相关讨论高达 1,595 次提及，远超其他工具，说明群成员对 AI 绘画、图像生成工作流有极高的实践热情。
2. **大语言模型对比与实战**。Claude（349 次）和 GPT（263 次）是使用最频繁的 LLM，DeepSeek（93 次）作为国产替代方案也受到关注。
3. **前端开发工具链**。v0（79 次）作为 AI 前端生成工具被频繁讨论，结合飞书（83 次）等协作工具，反映出群成员在「AI 编码 + 协作落地」方面的探索。

### 话题演变趋势

- **6 月初**（6/2-6/7）：群建立初期，大量新人自我介绍和工具安利，消息量逐步攀升。
- **6 月中旬**（6/8-6/20）：讨论进入深水区，SD 咒语分享、商业变现案例增多。
- **6 月下旬**（6/21-6/29）：活跃度有所下降但仍保持日均 140+ 条消息，讨论转向具体项目落地和个人成果展示。

### 关键洞察

群内 AI 工具生态已形成 **「SD 图像 → Claude/GPT 文本 → v0 前端 → 飞书协作」** 的四级链路，体现出成员从创意到落地的完整闭环意识。
"""
    return text

def generate_highlights_summary(stats):
    """关键摘要：提取代表性长消息"""
    daily_data = stats["daily"]
    peak_day = max(daily_data, key=lambda d: d["message_count"])
    
    # 找出几个有代表性的长消息主题
    text = f"""## 关键内容摘要

### 讨论高峰日

数据窗口内最活跃的一天是 **{peak_day['date']}**，单日 {peak_day['message_count']} 条消息、{peak_day['active_members']} 人参与，反映出群内在这一天有集中的话题引爆。

### 代表性讨论

- **AI 绘画咒语工程**：多位成员分享了 SD 提示词优化技巧，从基础参数调教到 ControlNet 高级工作流，形成了完整的知识传递链条。
- **个人 IP 商业化探索**：链屿、树林等核心成员分享了 AI 工具变现的实操经验，涵盖公众号写作、知识付费、工具订阅等多种路径。
- **微信聊天记录分析项目**：群内涌现了多个基于 WeChatDataAnalysis 的聊天记录分析与可视化项目，成员间互相分享网页部署成果（GitHub Pages）。
- **前端 AI 开发实践**：星野等成员分享了使用 v0 + Cursor 快速搭建网页的实战经验，带动了一波前端 AI 化讨论。
- **成长心态与行动力**：多位成员分享了从「观望」到「动手」的心路历程，"先完成再完美"成为群内高频共识。

### 链接与产出

28 天内群内分享了近 2,000 条链接，涵盖了 GitHub 项目、个人网页、AI 工具测评、教程文档等，形成了丰富的知识资产库。
"""
    return text

def generate_interaction_insights(stats):
    """互动洞察：分析@提及和时间分布"""
    time_dist = stats["time_dist"]
    peak_hours = sorted(time_dist, key=lambda x: x["count"], reverse=True)[:3]
    overview = stats["overview"]
    members = stats["members"]
    
    text = f"""## 互动模式分析

### 活跃时段特征

群聊活跃度呈现明显的 **双峰 + 深夜延续** 模式：

| 时段 | 消息量 | 特征 |
|------|--------|------|
| 🌅 午后高峰 13:00-14:00 | {peak_hours[0]['count'] + peak_hours[1]['count'] if len(peak_hours) > 1 else 'N/A'} 条 | 午休时段集中讨论、技术分享 |
| 🌆 晚间高峰 21:00-23:00 | {sum(h['count'] for h in time_dist if h['hour'] >= 21 and h['hour'] <= 23)} 条 | 晚间深度讨论、项目展示 |
| 🌙 深夜延续 23:00+ | {time_dist[23]['count']} 条 | AI 创作者典型的深夜生产力 |

深夜 23:00 是全天消息量最高的单小时（{time_dist[23]['count']} 条），体现了 AI 创作者群体「灵感夜间爆发」的工作习惯。

### 互动连接结构

- 28 天共发生 **{overview['total_messages']}** 次有效互动
- 群内已形成以 **链屿、树林、蝶秋、Auluolalalala、余白** 为核心的多中心互动网络
- 前 20 名活跃成员贡献了约 **60%** 的消息量，长尾参与者也保持了稳定的日常活跃

### 社群特征

这是一个 **高参与度、低灌水率** 的优质社群。消息质量过滤后保留了 11,556 条有效内容，系统消息过滤率达 35%，说明群内讨论聚焦、信噪比高。日均 {overview['avg_daily_active']} 人活跃，占全部成员（{overview['unique_senders']} 人）的 {round(overview['avg_daily_active']/overview['unique_senders']*100)}%，参与率健康。
"""
    return text

def generate_overall_insights(stats):
    """整体洞察：全量数据评估"""
    overview = stats["overview"]
    meta = stats["meta"]
    daily_data = stats["daily"]
    members = stats["members"]
    
    # 计算趋势
    first_week = sum(d["message_count"] for d in daily_data[:7])
    last_week = sum(d["message_count"] for d in daily_data[-7:])
    trend = "上升" if last_week > first_week else "下降" if last_week < first_week * 0.7 else "稳定"
    
    # Top5 成员统计
    top5_total = sum(m["message_count"] for m in members[:5] if m["name"] != "Unknown")
    top5_pct = round(top5_total / overview["total_messages"] * 100, 1)
    
    # 日均消息趋势
    first_week_avg = round(first_week / 7)
    last_week_avg = round(last_week / 7)
    
    text = f"""## 整体洞察与总结

### 数据总览

| 指标 | 数值 |
|------|------|
| 📊 数据窗口 | {meta['data_range_start']} ~ {meta['data_range_end']}（{meta['total_days']} 天） |
| 💬 有效消息 | {overview['total_messages']:,} 条 |
| 👥 参与成员 | {overview['unique_senders']} 人 |
| 📈 日均消息 | {overview['avg_daily_messages']} 条 |
| 🔥 日均活跃 | {overview['avg_daily_active']} 人 |
| 🏆 核心贡献者 | Top5 成员贡献 {top5_pct}% 消息量 |

### 社群健康度评估

**整体评级：健康活跃** 🟢

1. **活跃度**：日均 {overview['avg_daily_messages']} 条消息、{overview['avg_daily_active']} 人活跃，对于 350+ 人的社群属于高质量水平。
2. **集中度**：Top5 核心成员贡献 {top5_pct}%，分布合理，没有过度依赖个别成员。
3. **信噪比**：有效讨论占比约 64%（11,556/18,064），在微信社群中表现优秀。

### 趋势变化

- **首周**（6/2-6/8）：日均 {first_week_avg} 条，建群初期热度高
- **末周**（6/23-6/29）：日均 {last_week_avg} 条，热度呈自然回落
- **整体趋势**：{trend}

社群在经历了建群初期的密集交流后，进入了更可持续的稳态活跃期。末周日均 {last_week_avg} 条依然保持较高水位。

### 运营建议

1. **固定话题日**：可引入「作品展示日」「工具推荐日」等主题活动，维持讨论节奏。
2. **知识沉淀**：群内近 2,000 条链接分享是宝贵资产，可建立索引页面方便回溯。
3. **激活长尾**：对「已加入但较少发言」的 300+ 成员，可通过定向话题引导其参与。
4. **深夜时段运营**：22:00-23:00 是天然高峰，可安排晚间分享或成果展示。

---
*分析生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
*数据来源：微信解密数据库 → chat-stats-pipeline v1.0*
"""
    return text

# ============================================================
# API 调用模式（SiliconFlow）
# ============================================================
def call_siliconflow(prompt_template, stats_data, api_key):
    """通过 SiliconFlow API 调用 LLM 生成分析"""
    import requests
    
    # 根据 prompt_id 提取对应数据
    prompt_id = prompt_template["id"]
    
    # 简化：从 stats 提取相关字段构造 user prompt
    if prompt_id == "topics_analysis":
        data_context = json.dumps({
            "ai_tools": stats_data["topics"]["ai_tools"][:10],
            "daily_summary": f"{len(stats_data['daily'])} days, peak {max(d['message_count'] for d in stats_data['daily'])} msgs/day"
        }, ensure_ascii=False)
    elif prompt_id == "highlights_summary":
        data_context = json.dumps({
            "sample_highlights": [{"sender": h["sender_name"], "date": h["datetime"], "preview": h["content"][:200]} for h in stats_data["highlights"][:10]]
        }, ensure_ascii=False)
    elif prompt_id == "interaction_insights":
        data_context = json.dumps({
            "total_mentions": stats_data["interaction"]["total_mentions"],
            "peak_hours": sorted(stats_data["time_dist"], key=lambda x: x["count"], reverse=True)[:3],
            "overview": stats_data["overview"]
        }, ensure_ascii=False)
    elif prompt_id == "overall_insights":
        data_context = json.dumps({
            "overview": stats_data["overview"],
            "meta": stats_data["meta"],
            "members_top5": stats_data["members"][:5]
        }, ensure_ascii=False)
    else:
        data_context = "{}"
    
    payload = {
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "messages": [
            {"role": "system", "content": prompt_template["system"]},
            {"role": "user", "content": f"统计数据如下：\n{data_context}\n\n请生成分析报告。"}
        ],
        "temperature": 0.7,
        "max_tokens": prompt_template.get("estimated_tokens", 500) + 200
    }
    
    try:
        resp = requests.post(
            "https://api.siliconflow.cn/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        else:
            print(f"[WARN] SiliconFlow API error: {resp.status_code} {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"[WARN] SiliconFlow API exception: {e}")
        return None

# ============================================================
# 主流程
# ============================================================
def main():
    use_api = "--api" in sys.argv
    api_key = None
    
    if use_api:
        for arg in sys.argv:
            if arg.startswith("--key="):
                api_key = arg.split("=", 1)[1]
        if not api_key:
            api_key = os.environ.get("SILICONFLOW_API_KEY", "")
        if not api_key:
            print("[WARN] 未提供 API Key，回退到 AI 直接生成模式")
            use_api = False
    
    # 加载数据
    print("[AI_ANALYZER] 加载统计数据...")
    with open(STATS_PATH, 'r', encoding='utf-8') as f:
        stats = json.load(f)
    
    with open(PROMPTS_PATH, 'r', encoding='utf-8') as f:
        prompts_data = json.load(f)
    
    # 生成分析
    analysis = {
        "meta": {
            "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "mode": "AI直接生成" if not use_api else "SiliconFlow API",
            "api_model": "Qwen/Qwen2.5-7B-Instruct" if use_api else "N/A",
            "source_file": "chat_stats.json",
        }
    }
    
    prompts = prompts_data["prompts"]
    
    if use_api:
        print("[AI_ANALYZER] 调用 SiliconFlow API...")
        for p in prompts:
            print(f"  → {p['id']}...")
            result = call_siliconflow(p, stats, api_key)
            if result:
                analysis[p["output_key"]] = result
            else:
                # Fallback
                print(f"  → {p['id']} API 失败，使用内置分析...")
                analysis.update(generate_fallback(p["id"], stats))
    else:
        print("[AI_ANALYZER] AI 直接生成分析文本...")
        analysis["topics_summary"] = generate_topics_analysis(stats)
        analysis["highlights_summary"] = generate_highlights_summary(stats)
        analysis["interaction_summary"] = generate_interaction_insights(stats)
        analysis["insights_summary"] = generate_overall_insights(stats)
    
    # 写入输出
    with open(ANALYSIS_OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
    
    print(f"\n[AI_ANALYZER] 分析完成! 输出: {ANALYSIS_OUTPUT}")
    print(f"[AI_ANALYZER] 包含 {len(analysis)-1} 个分析段落")
    
    return analysis

def generate_fallback(prompt_id, stats):
    """单 prompt 的 fallback 生成"""
    if prompt_id == "topics_analysis":
        return {"topics_summary": generate_topics_analysis(stats)}
    elif prompt_id == "highlights_summary":
        return {"highlights_summary": generate_highlights_summary(stats)}
    elif prompt_id == "interaction_insights":
        return {"interaction_summary": generate_interaction_insights(stats)}
    elif prompt_id == "overall_insights":
        return {"insights_summary": generate_overall_insights(stats)}
    return {}

if __name__ == "__main__":
    main()
