from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.sql.expression import ColumnElement

from .config import settings
from .models import Video

# Upstream resource sites label adult material with recognisable category names
# ("日本无码", "探花嫖娼", "麻豆传媒", "91视频", "糖心Vlog" ...). Categories are
# matched broadly; titles are matched only on explicit porn words so ordinary
# dramas are not swept up by a word like "主播" or "学生".
DEFAULT_ADULT_TYPE_KEYWORDS = (
    "无码", "有码", "自拍", "偷拍", "偷窥", "盗摄", "嫖娼", "探花", "约炮", "乱伦",
    "强奸", "淫", "骚", "色情", "情色", "重口", "重咸", "三级", "伦理", "人妻", "熟女",
    "少妇", "巨乳", "美乳", "丝袜", "制服", "女优", "禁漫", "里番", "av", "sm", "裸聊",
    "裸", "做爱", "性爱", "口交", "颜射", "潮吹", "群交", "绿帽", "换妻", "福利",
    "主播", "网红", "传媒", "麻豆", "糖心", "杏吧", "海角", "抖阴", "黑料", "网曝",
    "网爆", "吃瓜", "门事件", "御姐", "萝莉", "母狗", "反差", "搭讪", "素人", "大秀",
    "秀色", "二区", "片商", "调教", "凌辱", "欺辱", "黑丝", "91", "同性恋", "成人",
    "激情", "诱惑", "私拍", "专区", "直播", "头条", "cosplay", "swag", "少女", "学生",
    "教师", "情趣", "风情", "露出", "空姐", "护士", "高潮", "变态", "另类", "极品",
    "精品", "熟妇", "童颜", "淫乱", "中文字幕", "vr", "尾随", "裙底", "足交", "口爆",
)

DEFAULT_ADULT_NAME_KEYWORDS = (
    "无码", "自拍", "偷拍", "偷窥", "盗摄", "嫖娼", "探花", "约炮", "乱伦", "强奸",
    "淫", "骚", "色情", "情色", "三级", "人妻", "熟女", "熟妇", "少妇", "巨乳", "丝袜",
    "女优", "全裸", "裸体", "裸聊", "做爱", "性爱", "口交", "颜射", "潮吹", "群交",
    "绿帽", "换妻", "福利姬", "麻豆", "糖心", "杏吧", "海角", "抖阴", "调教", "凌辱",
    "欺辱", "里番", "禁漫", "黑丝", "母狗", "自慰", "妓", "婊", "内射", "外射", "深喉",
    "双飞", "情趣", "露出", "3p", "4p", "同性", "女同", "御姐", "萝莉", "空姐", "护士",
    "黑料", "网曝", "门事件", "私拍", "高潮", "后入", "插入", "卖淫", "站街", "莞式",
    "毒龙", "骚妇", "淫乱", "淫荡", "吞精", "吃精", "性奴", "爆操", "狂操", "精液",
    "射精", "屌", "啪啪", "约啪", "裙底", "蜜桃臀", "鲍鱼", "粉嫩", "榨精", "肛",
    "女優", "性愛", "羞耻", "羞恥",
)


def adult_type_keywords() -> tuple[str, ...]:
    raw = settings.adult_type_keywords
    return tuple(raw) if raw is not None else DEFAULT_ADULT_TYPE_KEYWORDS


def adult_name_keywords() -> tuple[str, ...]:
    raw = settings.adult_name_keywords
    return tuple(raw) if raw is not None else DEFAULT_ADULT_NAME_KEYWORDS


def adult_condition() -> ColumnElement | None:
    """SQL condition matching videos classified as adult, or None if disabled."""
    terms = [Video.type_name.like(f"%{kw}%") for kw in adult_type_keywords()]
    terms += [Video.name.like(f"%{kw}%") for kw in adult_name_keywords()]
    # Adult aggregators almost never report a category at all, so an empty
    # type_name is treated as adult rather than "unknown".
    terms.append(Video.type_name.is_(None))
    terms.append(Video.type_name == "")
    return or_(*terms) if terms else None


def matched_keyword(video: Video) -> str | None:
    """Which keyword caught this record - useful for auditing the filter."""
    type_name = (video.type_name or "").lower()
    name = (video.name or "").lower()
    if not type_name:
        return "分类:未分类"
    for kw in adult_type_keywords():
        if kw.lower() in type_name:
            return f"分类:{kw}"
    for kw in adult_name_keywords():
        if kw.lower() in name:
            return f"标题:{kw}"
    return None


def is_adult(video: Video) -> bool:
    return matched_keyword(video) is not None
