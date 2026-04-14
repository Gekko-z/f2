# path: f2/apps/twitter/filter.py

from typing import Any

from f2.utils.json_filter import JSONModel
from f2.utils.utils import timestamp_2_str, replaceT, filter_to_list
from f2.apps.twitter.utils import extract_desc


# Filter


class TweetDetailFilter(JSONModel):
    """推文详情过滤器 - 适配 Twitter API 响应结构变化

    Twitter API 的 instructions 数组长度不固定（可能插入 TimelineClearCache 等），
    且返回结构频繁变化（如 result.tweet.legacy vs result.legacy）。
    因此初始化时动态查找包含 entries 的 instruction 索引，并用 _find_value()
    自动尝试多种路径结构来定位字段值。
    """

    def __init__(self, data):
        super().__init__(data)
        self._instruction_idx = self._find_instruction_index()

    def _find_instruction_index(self) -> str:
        """动态查找包含 entries 的 instruction 索引。

        先试 [0]，再试 [1]，最后遍历所有 instruction。
        返回格式如 "0"、"1" 等，用于拼接 jsonpath。
        """
        instructions = self._get_attr_value(
            "$.data.threaded_conversation_with_injections_v2.instructions"
        )
        if isinstance(instructions, list):
            # 先试 [0]，再试 [1]
            for idx in [0, 1]:
                if idx < len(instructions) and "entries" in instructions[idx]:
                    return str(idx)
            # 遍历所有 instruction
            for idx, inst in enumerate(instructions):
                if isinstance(inst, dict) and "entries" in inst:
                    return str(idx)
        # 兜底：返回 "0"，让 jsonpath 自己匹配失败
        return "0"

    def _find_value(self, legacy_field: str) -> Any:
        """自动查找字段值，尝试多种结构。

        Args:
            legacy_field: legacy 内的字段名，如 "id_str", "full_text"

        尝试顺序:
            1. result.tweet.legacy.{field}
            2. result.legacy.{field}
            3. 递归遍历 entries JSON 树查找匹配的 key
        """
        idx = self._instruction_idx
        base = (
            f"$.data.threaded_conversation_with_injections_v2.instructions[{idx}]"
            f".entries[0].content.itemContent.tweet_results.result"
        )

        # 第 1 次尝试：result.tweet.legacy.{field}
        path = f"{base}.tweet.legacy.{legacy_field}"
        value = self._get_attr_value(path)
        if value is not None:
            return value

        # 第 2 次尝试：result.legacy.{field}
        path = f"{base}.legacy.{legacy_field}"
        value = self._get_attr_value(path)
        if value is not None:
            return value

        # 第 3 次尝试：从 entries 根递归查找
        entries_path = (
            f"$.data.threaded_conversation_with_injections_v2.instructions[{idx}]"
            f".entries[0]"
        )
        entries = self._get_attr_value(entries_path)
        if entries:
            result = self._deep_search(entries, legacy_field)
            if result is not None:
                return result

        return None

    def _deep_search(self, obj: Any, target_key: str) -> Any:
        """在嵌套 JSON 中递归查找 target_key 对应的值。"""
        if isinstance(obj, dict):
            if target_key in obj:
                return obj[target_key]
            for v in obj.values():
                found = self._deep_search(v, target_key)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = self._deep_search(item, target_key)
                if found is not None:
                    return found
        return None

    # tweet
    @property
    def tweet_id(self):
        return self._find_value("id_str")

    @property
    def tweet_type(self):
        return self._get_attr_value(
            f"$.data.threaded_conversation_with_injections_v2.instructions[{self._instruction_idx}].entries[0].content.itemContent.itemType"
        )

    @property
    def tweet_views_count(self):
        idx = self._instruction_idx
        # 尝试 views.count (新版)
        value = self._get_attr_value(
            f"$.data.threaded_conversation_with_injections_v2.instructions[{idx}].entries[0].content.itemContent.tweet_results.result.tweet.views.count"
        )
        if value is not None:
            return value
        value = self._get_attr_value(
            f"$.data.threaded_conversation_with_injections_v2.instructions[{idx}].entries[0].content.itemContent.tweet_results.result.views.count"
        )
        if value is not None:
            return value
        return self._deep_search(
            self._get_attr_value(
                f"$.data.threaded_conversation_with_injections_v2.instructions[{idx}].entries[0]"
            ),
            "count"
        )

    # 收藏数
    @property
    def tweet_bookmark_count(self):
        return self._find_value("bookmark_count")

    # 点赞数
    @property
    def tweet_favorite_count(self):
        return self._find_value("favorite_count")

    # 评论数
    @property
    def tweet_reply_count(self):
        return self._find_value("reply_count")

    # 转推数
    @property
    def tweet_retweet_count(self):
        return self._find_value("retweet_count")

    # 发布时间
    @property
    def tweet_created_at(self):
        return timestamp_2_str(self._find_value("created_at"))

    # 推文内容
    @property
    def tweet_desc(self):
        return replaceT(extract_desc(self._find_value("full_text")))

    @property
    def tweet_desc_raw(self):
        return extract_desc(self._find_value("full_text"))

    # 媒体状态
    @property
    def tweet_media_status(self):
        return self._find_value("status")

    # 媒体类型
    @property
    def tweet_media_type(self):
        idx = self._instruction_idx
        base = (
            f"$.data.threaded_conversation_with_injections_v2.instructions[{idx}]"
            f".entries[0].content.itemContent.tweet_results.result"
        )
        # 尝试 tweet.legacy
        value = self._get_attr_value(f"{base}.tweet.legacy.entities.media[0].type")
        if value is not None:
            return value
        # 尝试 legacy
        value = self._get_attr_value(f"{base}.legacy.entities.media[0].type")
        if value is not None:
            return value
        return self._deep_search(
            self._get_attr_value(f"{base}"), "type"
        )

    # 图片链接（使用 extended_entities，与 video_info 保持一致的数据源）
    @property
    def tweet_media_url(self):
        media_info = self._get_unified_media_info()
        return [m["media_url"] for m in media_info]

    # 视频链接列表（多个视频时返回 URL 列表，每个视频取最高码率的 MP4）
    @property
    def tweet_video_urls(self):
        media_info = self._get_unified_media_info()
        return [m["video_url"] for m in media_info if m["video_url"]]

    def _get_unified_media_info(self) -> list:
        """
        从 extended_entities.media 统一解析所有媒体信息。
        返回 [{type, media_url, video_url}, ...] 列表。
        使用同一数据源确保 URL 和类型的索引一一对应。
        """
        idx = self._instruction_idx
        base = (
            f"$.data.threaded_conversation_with_injections_v2.instructions[{idx}]"
            f".entries[0].content.itemContent.tweet_results.result"
        )

        # 获取 extended_entities.media 数组
        all_media = self._get_attr_value(f"{base}.tweet.legacy.extended_entities.media")
        if not all_media:
            all_media = self._get_attr_value(f"{base}.legacy.extended_entities.media")
        if not all_media:
            all_media = self._get_attr_value(f"{base}.tweet.legacy.entities.media")
        if not all_media:
            all_media = self._get_attr_value(f"{base}.legacy.entities.media")

        if not all_media or not isinstance(all_media, list):
            return []

        results = []
        for media in all_media:
            if not isinstance(media, dict):
                continue

            media_type = media.get("type", "unknown")
            media_url = media.get("media_url_https", "")

            # 提取视频 URL
            video_url = None
            video_info = media.get("video_info", {})
            if video_info and isinstance(video_info, dict):
                variants = video_info.get("variants", [])
                if isinstance(variants, list):
                    mp4_variants = [
                        v for v in variants
                        if isinstance(v, dict) and v.get("content_type") == "video/mp4"
                    ]
                    if mp4_variants:
                        mp4_variants.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
                        video_url = mp4_variants[0].get("url")

            results.append({
                "type": media_type,
                "media_url": media_url,
                "video_url": video_url,
            })

        return results

    # 媒体类型列表（按顺序，与 tweet_media_url 索引对应）
    @property
    def tweet_media_types(self):
        media_info = self._get_unified_media_info()
        return [m["type"] for m in media_info]

    # 视频链接（兼容旧版，返回单个最高码率视频 URL）
    @property
    def tweet_video_url(self):
        video_urls = self.tweet_video_urls
        return video_urls[0] if video_urls else None

    # 视频时长
    @property
    def tweet_video_duration(self):
        idx = self._instruction_idx
        base = (
            f"$.data.threaded_conversation_with_injections_v2.instructions[{idx}]"
            f".entries[0].content.itemContent.tweet_results.result"
        )
        value = self._get_attr_value(f"{base}.tweet.legacy.extended_entities.media[*].video_info.duration_millis")
        if value is not None:
            return value
        return self._get_attr_value(f"{base}.legacy.extended_entities.media[*].video_info.duration_millis")

    # 视频码率
    @property
    def tweet_video_bitrate(self):
        idx = self._instruction_idx
        base = (
            f"$.data.threaded_conversation_with_injections_v2.instructions[{idx}]"
            f".entries[0].content.itemContent.tweet_results.result"
        )
        value = self._get_attr_value(f"{base}.tweet.legacy.extended_entities.media[*].video_info.variants[*].bitrate")
        if value is not None:
            return value
        return self._get_attr_value(f"{base}.legacy.extended_entities.media[*].video_info.variants[*].bitrate")

    # User
    # 注册时间
    @property
    def join_time(self):
        return timestamp_2_str(self._find_user_value("created_at"))

    # 蓝V认证
    @property
    def is_blue_verified(self):
        return self._find_user_value("is_blue_verified")

    # 用户ID example: VXNlcjoxNDkzODI0MTA2Njk2OTAwNjEx
    @property
    def user_id(self):
        return self._find_user_value("id")

    # 用户唯一ID（推特ID） example: Asai_chan_
    @property
    def user_unique_id(self):
        return self._find_user_value("screen_name")

    # 昵称 example: 核酸酱
    @property
    def nickname(self):
        return replaceT(self._find_user_value("name"))

    @property
    def nicename_raw(self):
        return self._find_user_value("name")

    @property
    def user_description(self):
        return replaceT(self._find_user_value("description"))

    @property
    def user_description_raw(self):
        return self._find_user_value("description")

    # 置顶推文ID
    @property
    def user_pined_tweet_id(self):
        return self._find_user_value("pinned_tweet_ids_str")

    # 主页背景图片
    @property
    def user_profile_banner_url(self):
        return self._find_user_value("profile_banner_url")

    # 关注者
    @property
    def followers_count(self):
        return self._find_user_value("followers_count")

    # 正在关注
    @property
    def friends_count(self):
        return self._find_user_value("friends_count")

    # 帖子数（推文数&回复 maybe？）
    @property
    def statuses_count(self):
        return self._find_user_value("statuses_count")

    # 媒体数（图片数&视频数）
    @property
    def media_count(self):
        return self._find_user_value("media_count")

    # 喜欢数
    @property
    def favourites_count(self):
        return self._find_user_value("favourites_count")

    @property
    def has_custom_timelines(self):
        return self._find_user_value("has_custom_timelines")

    @property
    def location(self):
        return self._find_user_value("location")

    @property
    def can_dm(self):
        return self._find_user_value("can_dm")

    def _find_user_value(self, target_key: str) -> Any:
        """自动查找用户相关字段值，适配不同 API 结构。

        尝试顺序:
            1. result.tweet.core.user_results.result.{key}
            2. result.core.user_results.result.{key}
            3. result.tweet.core.user_results.result.legacy.{key}
            4. result.core.user_results.result.legacy.{key}
            5. 递归遍历 entries 树
        """
        idx = self._instruction_idx
        base = (
            f"$.data.threaded_conversation_with_injections_v2.instructions[{idx}]"
            f".entries[0].content.itemContent.tweet_results.result"
        )

        paths_to_try = [
            f"{base}.tweet.core.user_results.result.{target_key}",
            f"{base}.core.user_results.result.{target_key}",
            f"{base}.tweet.core.user_results.result.legacy.{target_key}",
            f"{base}.core.user_results.result.legacy.{target_key}",
        ]

        for path in paths_to_try:
            value = self._get_attr_value(path)
            if value is not None:
                return value

        # 兜底：递归查找
        entries = self._get_attr_value(
            f"$.data.threaded_conversation_with_injections_v2.instructions[{idx}].entries[0]"
        )
        if entries:
            return self._deep_search(entries, target_key)

        return None

    def _to_raw(self) -> dict:
        return self._data

    def _to_dict(self) -> dict:
        return {
            prop_name: getattr(self, prop_name)
            for prop_name in dir(self)
            if not prop_name.startswith("__") and not prop_name.startswith("_")
        }


class UserProfileFilter(JSONModel):
    # User

    # 蓝V认证
    @property
    def is_blue_verified(self):
        return self._get_attr_value("$.data.user.result.is_blue_verified")

    # 用户ID example: VXNlcjoxNDkzODI0MTA2Njk2OTAwNjEx
    @property
    def user_id(self):
        return self._get_attr_value("$.data.user.result.id")

    # 获取主页需要这个rest_id
    @property
    def user_rest_id(self):
        return self._get_attr_value("$.data.user.result.rest_id")

    # 用户唯一ID（推特ID） example: Asai_chan_
    @property
    def user_unique_id(self):
        return self._get_attr_value("$.data.user.result.legacy.screen_name")

    # 注册时间
    @property
    def join_time(self):
        return timestamp_2_str(
            self._get_attr_value("$.data.user.result.legacy.created_at")
        )

    # 昵称 example: 核酸酱
    @property
    def nickname(self):
        return replaceT(self._get_attr_value("$.data.user.result.legacy.name"))

    @property
    def nickname_raw(self):
        return self._get_attr_value("$.data.user.result.legacy.name")

    @property
    def user_description(self):
        return replaceT(self._get_attr_value("$.data.user.result.legacy.description"))

    @property
    def user_description_raw(self):
        return self._get_attr_value("$.data.user.result.legacy.description")

    # 置顶推文ID
    @property
    def user_pined_tweet_id(self):
        return self._get_attr_value("$.data.user.result.legacy.pinned_tweet_ids_str[0]")

    # 主页背景图片
    @property
    def user_profile_banner_url(self):
        return self._get_attr_value("$.data.user.result.legacy.profile_banner_url")

    # 关注者
    @property
    def followers_count(self):
        return self._get_attr_value("$.data.user.result.legacy.followers_count")

    # 正在关注
    @property
    def friends_count(self):
        return self._get_attr_value("$.data.user.result.legacy.friends_count")

    # 帖子数（推文数&回复 maybe？）
    @property
    def statuses_count(self):
        return self._get_attr_value("$.data.user.result.legacy.statuses_count")

    # 媒体数（图片数&视频数）
    @property
    def media_count(self):
        return self._get_attr_value("$.data.user.result.legacy.media_count")

    # 喜欢数
    @property
    def favourites_count(self):
        return self._get_attr_value("$.data.user.result.legacy.favourites_count")

    @property
    def has_custom_timelines(self):
        return self._get_attr_value("$.data.user.result.legacy.has_custom_timelines")

    @property
    def location(self):
        return self._get_attr_value("$.data.user.result.legacy.location")

    @property
    def can_dm(self):
        return self._get_attr_value("$.data.user.result.legacy.can_dm")

    def _to_raw(self) -> dict:
        return self._data

    def _to_dict(self) -> dict:
        return {
            prop_name: getattr(self, prop_name)
            for prop_name in dir(self)
            if not prop_name.startswith("__") and not prop_name.startswith("_")
        }


class PostTweetFilter(JSONModel):
    # 用户发布的推文__typename是TweetWithVisibilityResults
    @property
    def cursorType(self):
        return self._get_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[-1].content.cursorType"
        )

    @property
    def min_cursor(self):
        return self._get_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[-2].content.value"
        )

    @property
    def max_cursor(self):
        return self._get_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[-1].content.value"
        )

    @property
    def entryId(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].entryId"
        )

    # tweet
    @property
    def tweet_id(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.conversation_id_str"
        )

    @property
    def tweet_created_at(self):
        create_times = self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.created_at"
        )
        return (
            [timestamp_2_str(str(ct)) for ct in create_times]
            if isinstance(create_times, list)
            else timestamp_2_str(str(create_times))
        )

    @property
    def tweet_favorite_count(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.favorite_count"
        )

    @property
    def tweet_reply_count(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.reply_count"
        )

    @property
    def tweet_retweet_count(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.retweet_count"
        )

    @property
    def tweet_quote_count(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.quote_count"
        )

    @property
    def tweet_views_count(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.views.count"
        )

    @property
    def tweet_desc(self):
        text_list = self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.full_text"
        )

        return replaceT(
            [
                extract_desc(text) if text and isinstance(text, str) else ""
                for text in text_list
            ]
        )

    @property
    def tweet_desc_raw(self):
        text_list = self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.full_text"
        )

        return [
            extract_desc(text) for text in text_list if text and isinstance(text, str)
        ]

    @property
    def tweet_media_status(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.entities.media[*].ext_media_availability.status"
        )

    @property
    def tweet_media_type(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.entities.media[0].type"
        )

    @property
    def tweet_media_url(self):
        media_lists = self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.entities.media"
        )

        return [
            (
                [
                    media["media_url_https"]
                    for media in media_list
                    if isinstance(media, dict) and "media_url_https" in media
                ]
                if media_list
                else None
            )
            for media_list in media_lists
        ]

    @property
    def tweet_video_url(self):

        video_url_lists = self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.entities.media"
        )

        return [
            (
                [
                    video_url["video_info"]["variants"][-1]["url"]
                    for video_url in video_url_list
                    if isinstance(video_url, dict) and "video_info" in video_url
                ]
                if video_url_list
                else None
            )
            for video_url_list in video_url_lists
        ]

    # user
    @property
    def user_id(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.id"
        )

    @property
    def is_blue_verified(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.is_blue_verified"
        )

    @property
    def user_created_at(self):
        create_times = self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.created_at"
        )
        return (
            [timestamp_2_str(str(ct)) for ct in create_times]
            if isinstance(create_times, list)
            else timestamp_2_str(str(create_times))
        )

    @property
    def user_description(self):
        return replaceT(
            self._get_list_attr_value(
                "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.description"
            )
        )

    @property
    def user_description_raw(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.description"
        )

    @property
    def user_location(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.location"
        )

    @property
    def user_friends_count(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.friends_count"
        )

    @property
    def user_followers_count(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.followers_count"
        )

    @property
    def user_favourites_count(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.favourites_count"
        )

    @property
    def user_media_count(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.media_count"
        )

    @property
    def user_statuses_count(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.statuses_count"
        )

    @property
    def nickname(self):
        return replaceT(
            self._get_list_attr_value(
                "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.name"
            )
        )

    @property
    def nickname_raw(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.name"
        )

    @property
    def user_screen_name(self):
        return replaceT(
            self._get_list_attr_value(
                "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.screen_name"
            )
        )

    @property
    def user_screen_name_raw(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.screen_name"
        )

    @property
    def user_profile_banner_url(self):
        return self._get_list_attr_value(
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.profile_banner_url"
        )

    def _to_raw(self) -> dict:
        return self._data

    def _to_dict(self) -> dict:
        return {
            prop_name: getattr(self, prop_name)
            for prop_name in dir(self)
            if not prop_name.startswith("__") and not prop_name.startswith("_")
        }

    def _to_list(self) -> list:
        exclude_fields = [
            "max_cursor",
            "min_cursor",
            "cursorType",
        ]

        extra_fields = [
            "max_cursor",
            "min_cursor",
        ]

        list_dicts = filter_to_list(
            self,
            "$.data.user.result.timeline_v2.timeline.instructions[-1].entries",
            exclude_fields,
            extra_fields,
        )

        return list_dicts


class LikeTweetFilter(PostTweetFilter):

    def __init__(self, data):
        super().__init__(data)


class BookmarkTweetFilter(JSONModel):

    # 用户发布的推文__typename是TweetWithVisibilityResults
    @property
    def cursorType(self):
        return self._get_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[-1].content.cursorType"
        )

    @property
    def min_cursor(self):
        return self._get_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[-2].content.value"
        )

    @property
    def max_cursor(self):
        return self._get_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[-1].content.value"
        )

    @property
    def entryId(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].entryId"
        )

    # tweet
    @property
    def tweet_id(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.conversation_id_str"
        )

    @property
    def tweet_created_at(self):
        create_times = self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.created_at"
        )
        return (
            [timestamp_2_str(str(ct)) for ct in create_times]
            if isinstance(create_times, list)
            else timestamp_2_str(str(create_times))
        )

    @property
    def tweet_favorite_count(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.favorite_count"
        )

    @property
    def tweet_reply_count(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.reply_count"
        )

    @property
    def tweet_retweet_count(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.retweet_count"
        )

    @property
    def tweet_quote_count(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.quote_count"
        )

    @property
    def tweet_views_count(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.views.count"
        )

    @property
    def tweet_desc(self):
        text_list = self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.full_text"
        )

        return replaceT(
            [
                extract_desc(text) if text and isinstance(text, str) else ""
                for text in text_list
            ]
        )

    @property
    def tweet_desc_raw(self):
        text_list = self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.full_text"
        )

        return [
            extract_desc(text) for text in text_list if text and isinstance(text, str)
        ]

    @property
    def tweet_media_status(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.entities.media[*].ext_media_availability.status"
        )

    @property
    def tweet_media_type(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.entities.media[0].type"
        )

    @property
    def tweet_media_url(self):
        media_lists = self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.entities.media"
        )

        return [
            (
                [
                    media["media_url_https"]
                    for media in media_list
                    if isinstance(media, dict) and "media_url_https" in media
                ]
                if media_list
                else None
            )
            for media_list in media_lists
        ]

    @property
    def tweet_video_url(self):

        video_url_lists = self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.legacy.entities.media"
        )

        return [
            (
                [
                    video_url["video_info"]["variants"][-1]["url"]
                    for video_url in video_url_list
                    if isinstance(video_url, dict) and "video_info" in video_url
                ]
                if video_url_list
                else None
            )
            for video_url_list in video_url_lists
        ]

    # user
    @property
    def user_id(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.id"
        )

    @property
    def is_blue_verified(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.is_blue_verified"
        )

    @property
    def user_created_at(self):
        create_times = self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.created_at"
        )
        return (
            [timestamp_2_str(str(ct)) for ct in create_times]
            if isinstance(create_times, list)
            else timestamp_2_str(str(create_times))
        )

    @property
    def user_description(self):
        return replaceT(
            self._get_list_attr_value(
                "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.description"
            )
        )

    @property
    def user_description_raw(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.description"
        )

    @property
    def user_location(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.location"
        )

    @property
    def user_friends_count(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.friends_count"
        )

    @property
    def user_followers_count(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.followers_count"
        )

    @property
    def user_favourites_count(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.favourites_count"
        )

    @property
    def user_media_count(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.media_count"
        )

    @property
    def user_statuses_count(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.statuses_count"
        )

    @property
    def nickname(self):
        return replaceT(
            self._get_list_attr_value(
                "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.name"
            )
        )

    @property
    def nickname_raw(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.name"
        )

    @property
    def user_screen_name(self):
        return replaceT(
            self._get_list_attr_value(
                "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.screen_name"
            )
        )

    @property
    def user_screen_name_raw(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.screen_name"
        )

    @property
    def user_profile_banner_url(self):
        return self._get_list_attr_value(
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries[*].content.itemContent.tweet_results.result.core.user_results.result.legacy.profile_banner_url"
        )

    def _to_raw(self) -> dict:
        return self._data

    def _to_dict(self) -> dict:
        return {
            prop_name: getattr(self, prop_name)
            for prop_name in dir(self)
            if not prop_name.startswith("__") and not prop_name.startswith("_")
        }

    def _to_list(self) -> list:
        exclude_fields = [
            "max_cursor",
            "min_cursor",
            "cursorType",
        ]

        extra_fields = [
            "max_cursor",
            "min_cursor",
        ]

        list_dicts = filter_to_list(
            self,
            "$.data.bookmark_timeline_v2.timeline.instructions[-1].entries",
            exclude_fields,
            extra_fields,
        )

        return list_dicts
