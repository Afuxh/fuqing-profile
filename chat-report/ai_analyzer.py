"""Generate a deterministic, privacy-safe analysis from aggregate chat statistics."""

import html
import json
import os
from datetime import datetime


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATS_PATH = os.path.join(SCRIPT_DIR, "chat_stats.json")
ANALYSIS_OUTPUT = os.path.join(SCRIPT_DIR, "chat_analysis.json")


def fmt_int(value):
    return f"{int(value or 0):,}"


def strongest_tools(stats, limit=5):
    return stats.get("topics", {}).get("ai_tools", [])[:limit]


def generate_topics_analysis(stats):
    tools = strongest_tools(stats)
    if not tools:
        return "<p>当前窗口没有足够的工具提及数据，暂不作主题判断。</p>"
    items = "".join(
        f"<li><strong>{html.escape(item['tool'])}</strong>：{fmt_int(item['count'])} 条消息提及</li>"
        for item in tools
    )
    return (
        "<p>本段只依据匿名聚合后的工具提及次数，不读取或展示任何成员原话。</p>"
        f"<ol>{items}</ol>"
        "<p>这些数字适合判断讨论重心，不等同于产品满意度、使用人数或商业价值。</p>"
    )


def generate_activity_summary(stats):
    daily = stats.get("daily", [])
    interaction = stats.get("interaction", {})
    if not daily:
        return "<p>当前窗口没有可用的日度数据。</p>"
    peak = max(daily, key=lambda row: row.get("message_count", 0))
    return (
        f"<p>窗口内活跃峰值出现在 <strong>{html.escape(peak['date'])}</strong>："
        f"{fmt_int(peak['message_count'])} 条分析消息、{fmt_int(peak['active_members'])} 个可归因发言者 ID 活跃。</p>"
        f"<p>共识别到 {fmt_int(interaction.get('shared_links'))} 次链接分享和 "
        f"{fmt_int(interaction.get('total_mentions'))} 次 @ 提及；公开报告只保留数量，不保留地址、参数或对象。</p>"
    )


def generate_interaction_insights(stats):
    time_dist = stats.get("time_dist", [])
    overview = stats.get("overview", {})
    concentration = stats.get("concentration", {})
    if not time_dist:
        return "<p>当前窗口没有可用的时段数据。</p>"
    peak = max(time_dist, key=lambda row: row.get("count", 0))
    return (
        f"<p>消息最高峰位于 <strong>{int(peak['hour']):02d}:00–{(int(peak['hour']) + 1) % 24:02d}:00</strong>，"
        f"共 {fmt_int(peak['count'])} 条。日均活跃的可归因发言者 ID 为 {fmt_int(overview.get('avg_daily_active'))} 个。</p>"
        f"<p>前 5 位匿名参与者贡献 {concentration.get('top5_message_share_pct', 0)}%，"
        f"前 20 位贡献 {concentration.get('top20_message_share_pct', 0)}%。该指标只用于观察讨论集中度，"
        "不用于成员排名。</p>"
    )


def generate_overall_insights(stats):
    overview = stats.get("overview", {})
    meta = stats.get("meta", {})
    daily = stats.get("daily", [])
    first = sum(row.get("message_count", 0) for row in daily[:7])
    last = sum(row.get("message_count", 0) for row in daily[-7:])
    first_days = min(7, len(daily)) or 1
    last_days = min(7, len(daily)) or 1
    first_avg = round(first / first_days)
    last_avg = round(last / last_days)
    if last_avg > first_avg * 1.15:
        trend = "上升"
    elif last_avg < first_avg * 0.85:
        trend = "回落"
    else:
        trend = "基本稳定"
    return (
        "<ul>"
        f"<li>数据窗口：{html.escape(meta.get('data_range_start', ''))} 至 "
        f"{html.escape(meta.get('data_range_end', ''))}，共 {fmt_int(meta.get('total_days'))} 天。</li>"
        f"<li>分析消息：{fmt_int(overview.get('total_messages'))} 条；可归因发言者 ID："
        f"{fmt_int(overview.get('unique_senders'))} 个；日均消息：{fmt_int(overview.get('avg_daily_messages'))} 条。</li>"
        f"<li>首个 7 日窗口日均 {fmt_int(first_avg)} 条，最近 7 日窗口日均 {fmt_int(last_avg)} 条，趋势为<strong>{trend}</strong>。</li>"
        "</ul>"
        "<p>建议把这份报告用于判断运营节奏和工具主题变化；任何涉及个人表现、敏感事件或原话的判断，"
        "都应回到私密版本并由人工复核。</p>"
    )


def main():
    with open(STATS_PATH, "r", encoding="utf-8") as handle:
        stats = json.load(handle)

    analysis = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "deterministic_aggregate",
            "privacy_mode": "anonymous_aggregate_v1",
            "source_file": "chat_stats.json",
        },
        "topics_summary": generate_topics_analysis(stats),
        "highlights_summary": generate_activity_summary(stats),
        "interaction_summary": generate_interaction_insights(stats),
        "insights_summary": generate_overall_insights(stats),
    }

    with open(ANALYSIS_OUTPUT, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(analysis, handle, ensure_ascii=False, indent=2)
    print(f"[AI_ANALYZER] 已生成匿名聚合分析: {ANALYSIS_OUTPUT}")
    return analysis


if __name__ == "__main__":
    main()
