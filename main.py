import asyncio
import time
from urllib.parse import quote

import httpx

from astrbot.api import logger
from astrbot.api import AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event import MessageChain
from astrbot.api.star import Context, Star, register

ITAD_LOOKUP = "https://api.isthereanydeal.com/games/lookup/v1"
ITAD_PRICES = "https://api.isthereanydeal.com/games/prices/v3"
GROUP_GAMES_PREFIX = "group_games_"
GROUP_GAMES_INDEX = "group_games_index"


@register(
    "steam_sale_monitor",
    "your_name",
    "Steam 游戏折扣监控，定时推送通知到群组，支持历史最低价判断",
    "1.0.0",
)
class SteamSalePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.running = False
        self.task = None
        self._cached_data = None
        self._cached_at = 0

    def _steam_api_url(self, path="/api/appdetails"):
        proxy = self.config.get("proxy_url", "").strip()
        if proxy:
            return proxy.rstrip("/") + path
        return f"https://store.steampowered.com{path}"

    def _steam_api_base(self):
        return self._steam_api_url("/api/appdetails")

    def _group_games_key(self, origin):
        return f"{GROUP_GAMES_PREFIX}{origin}"

    async def _get_group_games(self, origin):
        raw = await self.get_kv_data(self._group_games_key(origin), None)
        if not raw:
            return []
        return [x.strip() for x in raw.split(",") if x.strip()]

    async def _set_group_games(self, origin, ids):
        key = self._group_games_key(origin)
        if ids:
            await self.put_kv_data(key, ",".join(ids))
        else:
            await self.delete_kv_data(key)
        await self._update_group_index(origin, bool(ids))

    async def _update_group_index(self, origin, active):
        index = await self.get_kv_data(GROUP_GAMES_INDEX, [])
        changed = False
        if active and origin not in index:
            index.append(origin)
            changed = True
        elif not active and origin in index:
            index.remove(origin)
            changed = True
        if changed:
            await self.put_kv_data(GROUP_GAMES_INDEX, index)

    async def _collect_all_games(self):
        index = await self.get_kv_data(GROUP_GAMES_INDEX, [])
        all_ids = set()
        for origin in index:
            games = await self._get_group_games(origin)
            all_ids.update(games)
        return list(all_ids)

    async def _find_origins_for_appid(self, appid):
        index = await self.get_kv_data(GROUP_GAMES_INDEX, [])
        origins = []
        for origin in index:
            games = await self._get_group_games(origin)
            if appid in games:
                origins.append(origin)
        return origins

    async def _search_steam(self, term):
        url = self._steam_api_url("/api/search") + f"?term={quote(term)}&cc=CN&l=schinese"
        try:
            async with httpx.AsyncClient(timeout=self._get_timeout()) as c:
                resp = await c.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return [
                        {"appid": item.get("appid"), "name": item.get("name", "?"), "price": ""}
                        for item in data if item.get("appid")
                    ]
        except Exception as e:
            logger.warning(f"[SteamSale] Search error: {e}")
        return []

    def _get_timeout(self):
        return max(10, self.config.get("request_timeout", 120))

    async def _fetch_steam_data(self, ids, region):
        base = self._steam_api_base()

        async with httpx.AsyncClient(timeout=self._get_timeout()) as c:
            url = f"{base}?appids={','.join(ids)}&cc={region}"
            logger.info(f"[SteamSale] Fetching: {url}")
            resp = await c.get(url)
            body_preview = resp.text[:200]
            logger.info(
                f"[SteamSale] Response: {resp.status_code}"
                f" {body_preview}"
            )

            if resp.status_code == 200:
                data = resp.json()
                missing = [
                    a for a in ids
                    if a not in data or not data[a].get("success")
                ]
                if not missing:
                    return data
                logger.warning(
                    f"[SteamSale] Batch missing {len(missing)} games,"
                    f" fetching individually..."
                )
            else:
                logger.warning(
                    f"[SteamSale] Batch failed ({resp.status_code}),"
                    f" falling back to individual requests..."
                )

            async def fetch_one(appid):
                u = f"{base}?appids={appid}&cc={region}"
                r = await c.get(u)
                if r.status_code == 200:
                    return r.json().get(appid)
                return None

            tasks = [fetch_one(a) for a in ids]
            results = await asyncio.gather(*tasks)

            merged = {}
            for appid, result in zip(ids, results):
                if result:
                    merged[appid] = result
                else:
                    merged[appid] = {"success": False}
            return merged

    async def initialize(self):
        self.running = True
        self.task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self):
        while self.running:
            try:
                await self._check_discounts()
            except Exception as e:
                logger.error(f"[SteamSale] Poll error: {e}")
            interval = max(1, self.config.get("check_interval", 60))
            await asyncio.sleep(interval * 60)

    async def _check_discounts(self):
        ids = await self._collect_all_games()
        if not ids:
            return

        region = self.config.get("region", "cn")
        itad_key = self.config.get("itad_api_key", "").strip()

        steam_data = await self._fetch_steam_data(ids, region)
        if not steam_data or not any(
            v.get("success") for v in steam_data.values() if v
        ):
            return
        self._cached_data = steam_data
        self._cached_at = time.time()

        itad_lowest_map = {}
        if itad_key:
            try:
                async with httpx.AsyncClient(
                    timeout=self._get_timeout()
                ) as c:
                    itad_lowest_map = await self._fetch_itad_lowest(
                        c, ids, itad_key, region.upper()
                    )
            except Exception as e:
                logger.error(
                    f"[SteamSale] ITAD fetch error: {e}"
                )

        for appid_str in ids:
            try:
                await self._process_game(
                    steam_data,
                    appid_str,
                    itad_lowest_map.get(appid_str),
                )
            except Exception as e:
                logger.error(
                    f"[SteamSale] Error processing {appid_str}: {e}"
                )

    async def _fetch_itad_lowest(
        self, client, ids, itad_key, country
    ):
        uuid_to_appid = {}
        new_lookups = []

        for appid_str in ids:
            cached = await self.get_kv_data(f"itad_id_{appid_str}", None)
            if cached:
                uuid_to_appid[cached] = appid_str
            else:
                new_lookups.append(appid_str)

        for appid_str in new_lookups:
            try:
                resp = await client.get(
                    ITAD_LOOKUP,
                    params={
                        "appid": int(appid_str),
                        "key": itad_key,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if (
                        data.get("found")
                        and data.get("game", {}).get("id")
                    ):
                        uuid = data["game"]["id"]
                        uuid_to_appid[uuid] = appid_str
                        await self.put_kv_data(
                            f"itad_id_{appid_str}", uuid
                        )
            except Exception as e:
                logger.warning(
                    f"[SteamSale] ITAD lookup {appid_str}: {e}"
                )

        if not uuid_to_appid:
            return {}

        result = {}
        try:
            resp = await client.post(
                ITAD_PRICES,
                params={"key": itad_key, "country": country},
                json=list(uuid_to_appid.keys()),
            )
            if resp.status_code == 200:
                for entry in resp.json():
                    entry_id = entry.get("id")
                    appid = uuid_to_appid.get(entry_id)
                    if not appid:
                        continue
                    hl = entry.get("historyLow", {})
                    all_time = hl.get("all")
                    if all_time and all_time.get("amount") is not None:
                        result[appid] = float(all_time["amount"])
        except Exception as e:
            logger.warning(f"[SteamSale] ITAD prices error: {e}")

        return result

    async def _process_game(
        self, data, appid_str, itad_lowest
    ):
        app_entry = data.get(appid_str)
        if not app_entry or not app_entry.get("success"):
            return

        game = app_entry.get("data", {})
        name = game.get("name", f"App {appid_str}")
        price = game.get("price_overview")

        if not price or price.get("discount_percent", 0) <= 0:
            await self.delete_kv_data(f"notified_{appid_str}")
            return

        discount = price["discount_percent"]
        final_cents = price["final"]
        initial_cents = price["initial"]
        final_str = price.get(
            "final_formatted", f"¥{final_cents / 100:.2f}"
        )
        initial_str = price.get(
            "initial_formatted", f"¥{initial_cents / 100:.2f}"
        )

        key = f"notified_{appid_str}"
        existing = await self.get_kv_data(key, None)
        if existing:
            if (
                existing.get("discount") == discount
                and existing.get("final") == final_cents
            ):
                return

        current_final = final_cents / 100
        is_historical_low = False
        lowest_price = itad_lowest

        if lowest_price is not None and current_final <= lowest_price:
            is_historical_low = True

        msg = f"🎮 {name} 正在打折！\n"
        msg += f"原价：{initial_str}  现价：{final_str}  (-{discount}%)\n"
        if is_historical_low:
            msg += "🔥 历史最低价！不容错过！\n"
        elif lowest_price is not None:
            diff = round(current_final - lowest_price, 2)
            if diff > 0:
                msg += (
                    f"📊 历史最低价：¥{lowest_price}"
                    f"（当前高于最低 ¥{diff}）\n"
                )
            else:
                msg += f"📊 历史最低价：¥{lowest_price}\n"
        msg += f"🔗 https://store.steampowered.com/app/{appid_str}"

        targets = await self._find_origins_for_appid(appid_str)
        if targets:
            chain = MessageChain().message(msg).use_markdown(False)
            for target in targets:
                try:
                    await self.context.send_message(target, chain)
                except Exception as e:
                    logger.error(
                        f"[SteamSale] Failed to send to {target}: {e}"
                    )

        await self.put_kv_data(
            key,
            {
                "discount": discount,
                "final": final_cents,
                "initial": initial_cents,
                "name": name,
                "lowest": lowest_price,
                "timestamp": int(time.time()),
            },
        )

    @filter.command("steam_sale", alias={"折扣"})
    async def query_sales(self, event: AstrMessageEvent):
        """查询当前关注的 Steam 游戏折扣状态"""
        origin = event.unified_msg_origin
        ids = await self._get_group_games(origin)
        if not ids:
            yield event.plain_result(
                "⚠️ 本群未设置游戏。请使用 /搜索游戏 查找后，用 /添加游戏 添加。"
            ).use_markdown(False)
            return

        cache_age = time.time() - self._cached_at
        interval = max(1, self.config.get("check_interval", 60)) * 60
        if self._cached_data is not None and cache_age < interval:
            data = self._cached_data
        else:
            try:
                region = self.config.get("region", "cn")
                data = await self._fetch_steam_data(ids, region)
                if not data:
                    yield event.plain_result(
                        "⚠️ Steam API 请求失败，请稍后再试。"
                    ).use_markdown(False)
                    return
                self._cached_data = data
                self._cached_at = time.time()
            except httpx.TimeoutException:
                yield event.plain_result(
                    "⚠️ Steam API 请求超时，请稍后再试。"
                ).use_markdown(False)
                return
            except httpx.HTTPError as e:
                yield event.plain_result(
                    f"⚠️ 网络请求失败: {e}"
                ).use_markdown(False)
                return

        lines = ["📋 当前 Steam 游戏折扣状态：\n"]

        for appid_str in ids:
            app_entry = data.get(appid_str)
            if not app_entry or not app_entry.get("success"):
                lines.append(f"❌ App {appid_str} 获取失败")
                continue

            game = app_entry.get("data", {})
            name = game.get("name", f"App {appid_str}")
            price = game.get("price_overview")

            if price and price.get("discount_percent", 0) > 0:
                d = price["discount_percent"]
                f = price.get(
                    "final_formatted", f"¥{price['final'] / 100:.2f}"
                )
                i = price.get(
                    "initial_formatted",
                    f"¥{price['initial'] / 100:.2f}",
                )
                lines.append(f"🎮 {name}  -{d}%\n   {i} → {f}")
            elif price:
                cur = price.get(
                    "final_formatted", f"¥{price['final'] / 100:.2f}"
                )
                lines.append(f"🎮 {name}\n   现价 {cur}")
            else:
                lines.append(f"🎮 {name}\n   暂无价格信息")

        yield event.plain_result("\n".join(lines)).use_markdown(False)

    @filter.command("steam_add", alias={"添加游戏"})
    async def add_game(self, event: AstrMessageEvent):
        """向本群游戏列表添加游戏，例：/添加游戏 730"""
        parts = event.message_str.strip().split()
        if len(parts) < 2 or not parts[-1].isdigit():
            yield event.plain_result("⚠️ 用法：/添加游戏 <App ID>，如 /添加游戏 730").use_markdown(False)
            return
        appid = parts[-1]
        origin = event.unified_msg_origin
        games = await self._get_group_games(origin)
        if appid in games:
            yield event.plain_result(f"ℹ️ App {appid} 已在列表中。").use_markdown(False)
            return
        games.append(appid)
        await self._set_group_games(origin, games)
        yield event.plain_result(f"已添加 App {appid} 到本群游戏列表。").use_markdown(False)

    @filter.command("steam_remove", alias={"移除游戏"})
    async def remove_game(self, event: AstrMessageEvent):
        """从本群游戏列表移除游戏，例：/移除游戏 730"""
        parts = event.message_str.strip().split()
        if len(parts) < 2 or not parts[-1].isdigit():
            yield event.plain_result("⚠️ 用法：/移除游戏 <App ID>，如 /移除游戏 730").use_markdown(False)
            return
        appid = parts[-1]
        origin = event.unified_msg_origin
        games = await self._get_group_games(origin)
        if appid not in games:
            yield event.plain_result(f"ℹ️ App {appid} 不在本群列表中。").use_markdown(False)
            return
        games.remove(appid)
        await self._set_group_games(origin, games)
        yield event.plain_result(f"✅ 已从本群列表移除 App {appid}。").use_markdown(False)

    @filter.command("steam_list", alias={"游戏列表"})
    async def list_games(self, event: AstrMessageEvent):
        """查看本群关注的游戏列表"""
        origin = event.unified_msg_origin
        games = await self._get_group_games(origin)
        if not games:
            msg = "📋 本群未设置任何游戏。使用 /添加游戏 添加。"
        else:
            msg = "📋 本群关注的游戏：\n" + "\n".join(f"  App {g}" for g in games)
        yield event.plain_result(msg).use_markdown(False)

    @filter.command("steam_search", alias={"搜索游戏"})
    async def search_game(self, event: AstrMessageEvent):
        """搜索 Steam 游戏，例：/搜索游戏 荒野大镖客"""
        parts = event.message_str.strip().split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("⚠️ 用法：/搜索游戏 <关键词>，如 /搜索游戏 荒野大镖客").use_markdown(False)
            return
        term = parts[1]
        results = await self._search_steam(term)
        if not results:
            yield event.plain_result(f"❌ 未找到与「{term}」匹配的游戏。").use_markdown(False)
            return
        lines = [f"🔍 搜索「{term}」的结果：\n"]
        for r in results[:10]:
            lines.append(f"  {r['name']}  ({r['appid']})")
        lines.append("\n使用 /添加游戏 <App ID> 添加到本群。")
        yield event.plain_result("\n".join(lines)).use_markdown(False)

    async def terminate(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
