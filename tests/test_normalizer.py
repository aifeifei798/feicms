from __future__ import annotations

from feicms.normalizer import (
    build_signature,
    normalize_item,
    parse_play_url,
    parse_response,
)


def test_parse_play_url_dollar_separator():
    plays = parse_play_url("example", "第01集$https://a/1.m3u8#第02集$https://a/2.m3u8")
    assert len(plays) == 1
    assert plays[0].from_name == "example"
    assert [e.name for e in plays[0].episodes] == ["第01集", "第02集"]
    assert plays[0].episodes[1].url == "https://a/2.m3u8"


def test_parse_play_url_colon_separator_and_multiple_sources():
    plays = parse_play_url(
        "bfzy,myzy",
        "第1集:https://a/1.mp4#第2集:https://a/2.mp4,第1集:https://b/1.mp4",
    )
    assert len(plays) == 2
    assert plays[0].from_name == "bfzy"
    assert plays[1].from_name == "myzy"
    assert len(plays[1].episodes) == 1


def test_parse_play_url_skips_non_http():
    plays = parse_play_url("x", "第1集$not-a-url#第2集$https://ok/1.mp4")
    assert len(plays) == 1
    assert len(plays[0].episodes) == 1


def test_parse_play_url_dollar_dollar_dollar_lines():
    # Most sites put several play lines in one field, separated by `$$$`.
    plays = parse_play_url(
        "hnyun$$$hnm3u8",
        "全集完结$https://hn.bfvvs.com/play/bqxVlKRa$$$全集完结$https://hn.bfvvs.com/play/bqxVlKRa/index.m3u8",
    )
    assert [p.from_name for p in plays] == ["hnyun", "hnm3u8"]
    assert plays[0].episodes[0].url == "https://hn.bfvvs.com/play/bqxVlKRa"
    assert plays[1].episodes[0].url == "https://hn.bfvvs.com/play/bqxVlKRa/index.m3u8"


def test_parse_play_url_bare_urls_get_episode_names():
    plays = parse_play_url("ggsndf", "https://a/1.m3u8#https://a/2.m3u8#https://a/1.m3u8")
    assert len(plays) == 1
    assert [e.name for e in plays[0].episodes] == ["正片", "第2集"]
    assert len(plays[0].episodes) == 2


def test_normalize_item_basic():
    item = normalize_item(
        {
            "vod_id": 12,
            "vod_name": "测试影片 2024",
            "type_name": "电影",
            "vod_pic": "https://img/p.jpg",
            "vod_play_from": "src",
            "vod_play_url": "正片$https://v/1.mp4",
        }
    )
    assert item is not None
    assert item.vod_id == 12
    assert item.year == "2024"
    assert item.signature == build_signature("测试影片 2024", "2024")
    assert item.has_playable()


def test_normalize_item_rejects_missing_id():
    assert normalize_item({"vod_name": "no id"}) is None


def test_parse_response_json():
    payload = '{"code":1,"pagecount":3,"total":60,"list":[{"vod_id":1,"vod_name":"A","vod_play_url":"正片$https://x/1.mp4"}]}'
    items, pagecount, total = parse_response(payload, "json")
    assert len(items) == 1
    assert pagecount == 3
    assert total == 60


def test_parse_response_xml_rss():
    payload = (
        '<?xml version="1.0" encoding="utf-8"?><rss version="5.1">'
        '<list page="1" pagecount="10" pagesize="20" recordcount="200">'
        '<video><last>2024-01-01 00:00:00</last><id>7</id><tid>3</tid>'
        '<name><![CDATA[示例视频]]></name><type>日韩</type><dt>example3u8</dt>'
        '<note><![CDATA[HD]]></note></video></list></rss>'
    )
    items, pagecount, total = parse_response(payload, "xml")
    assert len(items) == 1
    assert items[0].vod_id == 7
    assert items[0].type_name == "日韩"
    assert pagecount == 10
    assert total == 200


def test_signature_is_stable_across_spacing_and_year_suffix():
    assert build_signature("神兽学院 2026", "2026") == build_signature("神兽学院2026", "")