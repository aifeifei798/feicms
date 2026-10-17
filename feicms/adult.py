from __future__ import annotations

import hashlib
import re

from sqlalchemy import event, func, or_, select, text
from sqlalchemy.sql.expression import ColumnElement

from .config import settings
from .models import Setting, Video

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


def matches_adult_keywords(name: str, type_name: str) -> bool:
    """Keyword sweep on raw strings - used when a record is created/updated.

    A single compiled alternation is used instead of 200 substring checks so the
    collector stays cheap on a full sweep.
    """
    type_name = (type_name or "").lower()
    if not type_name:
        return True
    return _type_pattern().search(type_name) is not None or _name_pattern().search(name) is not None


def _keyword_pattern(keywords: tuple[str, ...]) -> re.Pattern:
    keywords = sorted({k for k in keywords if k}, key=len, reverse=True)
    if not keywords:
        return re.compile(r"(?!)")  # never matches
    return re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE)


_TYPE_PATTERN: dict[str, re.Pattern] = {}
_NAME_PATTERN: dict[str, re.Pattern] = {}


def _type_pattern() -> re.Pattern:
    signature = keyword_signature()
    pattern = _TYPE_PATTERN.get(signature)
    if pattern is None:
        pattern = _keyword_pattern(adult_type_keywords())
        _TYPE_PATTERN[signature] = pattern
    return pattern


def _name_pattern() -> re.Pattern:
    signature = keyword_signature()
    pattern = _NAME_PATTERN.get(signature)
    if pattern is None:
        pattern = _keyword_pattern(adult_name_keywords())
        _NAME_PATTERN[signature] = pattern
    return pattern


def is_adult(video: Video) -> bool:
    return matched_keyword(video) is not None


def flag_condition() -> ColumnElement:
    """Indexed condition for "this record is adult" - use this in queries.

    `adult_condition()` is the keyword sweep (a full scan); `flag_condition()`
    reads the materialised `videos.is_adult` column, which is indexed.
    """
    return Video.is_adult.is_(True)


def hide_adult_condition(hide_adult: bool) -> ColumnElement | None:
    return Video.is_adult.is_(False) if hide_adult else None


def keyword_signature() -> str:
    """Stable hash of the active keyword lists, used to know when a refresh is due."""
    payload = ",".join(adult_type_keywords()) + "|" + ",".join(adult_name_keywords())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _escape(keyword: str) -> str:
    return keyword.replace("'", "''").replace("%", "").replace("_", "")


@event.listens_for(Video, "before_insert")
def _flag_on_insert(_mapper, _connection, video: Video) -> None:
    """Keep the indexed flag in sync with the keyword lists on every write."""
    video.is_adult = matches_adult_keywords(video.name, video.type_name)


@event.listens_for(Video, "before_update")
def _flag_on_update(_mapper, _connection, video: Video) -> None:
    video.is_adult = matches_adult_keywords(video.name, video.type_name)


def adult_sql_condition() -> str | None:
    """Raw SQL for the keyword sweep, used only to (re)fill `videos.is_adult`."""
    terms = [f"type_name LIKE '%{_escape(kw)}%'" for kw in adult_type_keywords()]
    terms += [f"name LIKE '%{_escape(kw)}%'" for kw in adult_name_keywords()]
    terms.append("type_name IS NULL")
    terms.append("type_name = ''")
    return "(" + " OR ".join(terms) + ")"


async def refresh_adult_flags(force: bool = False) -> dict[str, object]:
    """Materialise the keyword sweep into `videos.is_adult`.

    The front site filters on the indexed flag; the 200-term OR of LIKEs cannot
    use an index, so it is only ever run here - once at init, and again whenever
    the keyword lists change.
    """
    from .db import session

    signature = keyword_signature()
    async with session() as s:
        stored = await s.scalar(select(Setting).where(Setting.key == "adult_keyword_signature"))
        if stored is not None and stored.value == signature and not force:
            return {"refreshed": False, "adult": None}

        condition = adult_sql_condition()
        if condition is None:
            await s.execute(text("UPDATE videos SET is_adult = 0"))
        else:
            await s.execute(
                text(f"UPDATE videos SET is_adult = CASE WHEN {condition} THEN 1 ELSE 0 END")
            )
        adult = await s.scalar(select(func.count(Video.id)).where(Video.is_adult.is_(True)))
        if stored is None:
            s.add(Setting(key="adult_keyword_signature", value=signature))
        else:
            stored.value = signature
        await s.commit()
    return {"refreshed": True, "adult": adult or 0}
