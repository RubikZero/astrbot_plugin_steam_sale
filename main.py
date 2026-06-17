import asyncio
import time

import httpx

from astrbot.api import logger
from astrbot.api import AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event import MessageChain
from astrbot.api.star import Context, Star, register

STEAM_API = "https://store.steampowered.com/api/appdetails"
ITAD_LOOKUP = "https://api.isthereanydeal.com/games/lookup/v1"
ITAD_PRICES = "https://api.isthereanydeal.com/games/prices/v3"
SUBS_KEY = "subscriptions"


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

    def _get_timeout(self):
        return max(10, self.config.get("request_timeout", 120))

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
        game_ids_str = self.config.get("steam_game_ids", "").strip()
        if not game_ids_str:
            return
        ids = [x.strip() for x in game_ids_str.split(",") if x.strip()]
        if not ids:
            return

        region = self.config.get("region", "cn")
        itad_key = self.config.get("itad_api_key", "").strip()

        async with httpx.AsyncClient(timeout=self._get_timeout()) as c:
            resp = await c.get(
                STEAM_API, params={"appids": ",".join(ids), "cc": region}
            )
            if resp.status_code != 200:
                logger.warning(
                    f"[SteamSale] Steam API returned {resp.status_code}"
                )
                return
            steam_data = resp.json()
            self._cached_data = steam_data
            self._cached_at = time.time()

            itad_lowest_map = {}
            if itad_key:
                try:
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

        subs = await self.get_kv_data(SUBS_KEY, [])
        if subs:
            chain = MessageChain().message(msg)
            valid_subs = []
            for sub in subs:
                try:
                    await self.context.send_message(sub, chain)
                    valid_subs.append(sub)
                except Exception as e:
                    logger.error(
                        f"[SteamSale] Failed to send to {sub}: {e}"
                    )
            await self.put_kv_data(SUBS_KEY, valid_subs)

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

    @filter.command("steam_sub")
    async def subscribe(self, event: AstrMessageEvent):
        """订阅本群的 Steam 折扣通知"""
        async for r in self._do_subscribe(event):
            yield r

    @filter.command("订阅折扣")
    async def subscribe_cn(self, event: AstrMessageEvent):
        async for r in self._do_subscribe(event):
            yield r

    async def _do_subscribe(self, event):
        origin = event.unified_msg_origin
        subs = await self.get_kv_data(SUBS_KEY, [])
        if origin not in subs:
            subs.append(origin)
            await self.put_kv_data(SUBS_KEY, subs)
            yield event.plain_result(
                "✅ 已订阅 Steam 折扣通知，将在关注游戏打折时收到推送。"
            )
        else:
            yield event.plain_result(
                "ℹ️ 本群/频道已订阅过 Steam 折扣通知。"
            )

    @filter.command("steam_unsub")
    async def unsubscribe(self, event: AstrMessageEvent):
        """取消订阅本群的 Steam 折扣通知"""
        async for r in self._do_unsubscribe(event):
            yield r

    @filter.command("取消订阅")
    async def unsubscribe_cn(self, event: AstrMessageEvent):
        async for r in self._do_unsubscribe(event):
            yield r

    async def _do_unsubscribe(self, event):
        origin = event.unified_msg_origin
        subs = await self.get_kv_data(SUBS_KEY, [])
        if origin in subs:
            subs.remove(origin)
            await self.put_kv_data(SUBS_KEY, subs)
            yield event.plain_result("✅ 已取消订阅 Steam 折扣通知。")
        else:
            yield event.plain_result(
                "ℹ️ 本群/频道未订阅 Steam 折扣通知。"
            )

    @filter.command("steam_sale")
    async def query_sales(self, event: AstrMessageEvent):
        """查询当前关注的 Steam 游戏折扣状态"""
        async for r in self._do_query_sales(event):
            yield r

    @filter.command("折扣")
    async def query_sales_cn(self, event: AstrMessageEvent):
        async for r in self._do_query_sales(event):
            yield r

    async def _do_query_sales(self, event):
        game_ids_str = self.config.get("steam_game_ids", "").strip()
        if not game_ids_str:
            yield event.plain_result(
                "⚠️ 未配置 Steam 游戏 ID。请在插件设置中添加。"
            )
            return

        ids = [x.strip() for x in game_ids_str.split(",") if x.strip()]

        cache_age = time.time() - self._cached_at
        interval = max(1, self.config.get("check_interval", 60)) * 60
        if self._cached_data is not None and cache_age < interval:
            data = self._cached_data
        else:
            region = self.config.get("region", "cn")
            try:
                async with httpx.AsyncClient(
                    timeout=self._get_timeout()
                ) as c:
                    resp = await c.get(
                        STEAM_API,
                        params={"appids": ",".join(ids), "cc": region},
                    )
                    if resp.status_code != 200:
                        yield event.plain_result(
                            "⚠️ Steam API 请求失败，请稍后再试。"
                        )
                        return
                    data = resp.json()
                    self._cached_data = data
                    self._cached_at = time.time()
            except httpx.TimeoutException:
                yield event.plain_result(
                    "⚠️ Steam API 请求超时，请稍后再试。"
                )
                return
            except httpx.HTTPError as e:
                yield event.plain_result(
                    f"⚠️ 网络请求失败: {e}"
                )
                return

        lines = ["📋 当前 Steam 游戏折扣状态：\n"]
        found_sale = False

        for appid_str in ids:
            app_entry = data.get(appid_str)
            if not app_entry or not app_entry.get("success"):
                lines.append(f"❌ App {appid_str} 获取失败")
                continue

            game = app_entry.get("data", {})
            name = game.get("name", f"App {appid_str}")
            price = game.get("price_overview")

            if price and price.get("discount_percent", 0) > 0:
                found_sale = True
                d = price["discount_percent"]
                f = price.get(
                    "final_formatted", f"¥{price['final'] / 100:.2f}"
                )
                i = price.get(
                    "initial_formatted",
                    f"¥{price['initial'] / 100:.2f}",
                )
                lines.append(f"🎮 {name}  -{d}%\n   {i} → {f}")
            else:
                lines.append(f"❌ {name}  无折扣")

        if not found_sale:
            yield event.plain_result("📋 当前关注的游戏均无折扣。")
        else:
            yield event.plain_result("\n".join(lines))

    async def terminate(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
