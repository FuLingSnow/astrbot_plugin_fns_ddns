import asyncio
import json
import re
import time

import requests
from aliyunsdkalidns.request.v20150109 import (
    AddDomainRecordRequest,
    DeleteDomainRecordRequest,
    DescribeSubDomainRecordsRequest,
    UpdateDomainRecordRequest,
)
from aliyunsdkcore.client import AcsClient
from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context, Star, register


class DDNSConfig:
    def __init__(self, config: dict):
        self.access_key_id = re.sub(r"\s+", "", config.get("access_key_id", ""))
        self.access_key_secret = re.sub(r"\s+", "", config.get("access_key_secret", ""))
        self.domain = re.sub(r"\s+", "", config.get("domain", "")).lower()
        self.sub_domain = re.sub(r"\s+", "", config.get("sub_domain", ""))
        self.check_interval = max(30, int(config.get("check_interval", 300) or 300))
        self.min_cool_second = max(0, int(config.get("min_cool_second", 60) or 60))

        if not self.sub_domain:
            self.sub_domain = "@"
            logger.warning("[DDNS] sub_domain 为空，自动设为 @（主域名）")

    @classmethod
    def from_dict(cls, config: dict):
        return cls(config)

    def is_ready(self) -> bool:
        return bool(self.access_key_id and self.access_key_secret and self.domain)

    def get_full_domain(self) -> str:
        if self.sub_domain == "@":
            return self.domain
        return f"{self.sub_domain}.{self.domain}"

    def get_rr(self) -> str:
        return self.sub_domain


@register("fns_ddns", "FuLingSnow", "从 API 查询IP调整域名解析", "v1.0.2")
class DDNSPlugin(Star):
    # 当前 DDNS 循环任务
    _current_task: asyncio.Task | None = None

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self._raw_config = config
        self.config = DDNSConfig.from_dict(config)

        if not self.config.is_ready():
            logger.error("[DDNS] 配置缺失 AK / Secret / domain")
            return

        self.client = AcsClient(
            ak=self.config.access_key_id,
            secret=self.config.access_key_secret,
            region_id="cn-hangzhou",
        )

        self._last_update_time: float = 0
        self._update_lock = asyncio.Lock()

        logger.info("[DDNS] ========== 配置信息 ==========")
        logger.info(f"[DDNS] 主域名: {self.config.domain}")
        logger.info(f"[DDNS] 子域名: {self.config.sub_domain}")
        logger.info(f"[DDNS] 完整域名: {self.config.get_full_domain()}")
        logger.info(f"[DDNS] RR: {self.config.get_rr()}")
        logger.info(f"[DDNS] 检查间隔: {self.config.check_interval}s")
        logger.info(f"[DDNS] 最小更新冷却: {self.config.min_cool_second}s")
        logger.info("[DDNS] 仅 IPv4 模式")
        logger.info("[DDNS] =================================")

        # 取消仍在运行的旧任务
        if DDNSPlugin._current_task and not DDNSPlugin._current_task.done():
            logger.info("[DDNS] 检测到旧循环任务仍在运行，正在取消...")
            DDNSPlugin._current_task.cancel()

        DDNSPlugin._current_task = asyncio.create_task(self._ddns_loop())

    async def _ddns_loop(self):
        """DDNS 主循环，支持取消"""
        try:
            while True:
                try:
                    current_ip = await self._get_public_ipv4()
                    if not current_ip:
                        logger.warning("[DDNS] 获取IPv4失败，跳过本次检测")
                        await asyncio.sleep(self.config.check_interval)
                        continue
                    logger.info(f"[DDNS] 当前公网IPv4: {current_ip}")

                    now = time.time()
                    if now - self._last_update_time < self.config.min_cool_second:
                        remain = self.config.min_cool_second - (
                            now - self._last_update_time
                        )
                        logger.debug(f"[DDNS] 更新冷却中，剩余 {remain:.0f}s")
                        await asyncio.sleep(self.config.check_interval)
                        continue

                    async with self._update_lock:
                        await self._check_and_update(current_ip)

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"[DDNS] 主循环异常: {e}", exc_info=True)

                await asyncio.sleep(self.config.check_interval)
        except asyncio.CancelledError:
            logger.info("[DDNS] 主循环被取消，正在退出...")
        finally:
            logger.info("[DDNS] 主循环已退出")

    async def terminate(self):
        """卸载时取消任务并清理"""
        logger.info("[DDNS] 收到卸载/重载信号，准备停止DDNS循环")
        if DDNSPlugin._current_task and not DDNSPlugin._current_task.done():
            DDNSPlugin._current_task.cancel()
            try:
                await DDNSPlugin._current_task
            except asyncio.CancelledError:
                logger.info("[DDNS] DDNS后台任务已成功取消")
            DDNSPlugin._current_task = None
        logger.info("[DDNS] DDNS监控任务完全停止")

    async def _get_public_ipv4(self) -> str | None:
        services = [
            "https://api.ipify.org",
            "https://ip4.seeip.org",
            "https://ip4.now/",
        ]
        timeout = 5
        for url in services:
            try:
                loop = asyncio.get_running_loop()
                resp = await loop.run_in_executor(
                    None, lambda u=url: requests.get(u, timeout=timeout)
                )
                try:
                    if resp.status_code != 200:
                        continue
                    raw = resp.text.strip()
                    match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", raw)
                    if not match:
                        continue
                    ip = match.group(0)
                    if self._is_public_ipv4(ip):
                        return ip
                    logger.warning(f"[DDNS] {url} 返回非公网地址 {ip}，忽略")
                finally:
                    resp.close()
            except requests.exceptions.RequestException as e:
                logger.debug(f"[DDNS] {url} 请求异常: {e!s}")
                continue
        logger.error("[DDNS] 全部IPv4查询接口请求失败")
        return None

    @staticmethod
    def _is_valid_ipv4(ip: str) -> bool:
        """校验 IPv4 格式（四段 0-255），不校验是否公网。"""
        if ":" in ip:
            return False
        if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ip):
            return False
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        for seg in parts:
            if not seg.isdigit():
                return False
            n = int(seg)
            if n < 0 or n > 255:
                return False
        return True

    @staticmethod
    def _is_public_ipv4(ip: str) -> bool:
        """校验 IP 是否为公网 IPv4，排除私网、回环、保留等网段。"""
        if not DDNSPlugin._is_valid_ipv4(ip):
            return False
        a, b, _, _ = (int(x) for x in ip.split("."))

        # 0.0.0.0/8 本网络
        if a == 0:
            return False
        # 10.0.0.0/8 私网
        if a == 10:
            return False
        # 100.64.0.0/10 CGNAT
        if a == 100 and 64 <= b <= 127:
            return False
        # 127.0.0.0/8 回环
        if a == 127:
            return False
        # 169.254.0.0/16 链路本地
        if a == 169 and b == 254:
            return False
        # 172.16.0.0/12 私网
        if a == 172 and 16 <= b <= 31:
            return False
        # 192.168.0.0/16 私网
        if a == 192 and b == 168:
            return False
        # 224.0.0.0/4 组播
        if 224 <= a <= 239:
            return False
        # 240.0.0.0/4 保留
        return not 240 <= a <= 255

    async def _get_all_a_records(self) -> list[dict] | None:
        """查询目标域名 A 记录，失败返回 None。"""
        retry = 2
        domain = self.config.get_full_domain()
        while retry >= 0:
            try:
                req = DescribeSubDomainRecordsRequest.DescribeSubDomainRecordsRequest()
                req.set_SubDomain(domain)
                req.set_Type("A")
                loop = asyncio.get_running_loop()
                res_raw = await loop.run_in_executor(
                    None, lambda r=req: self.client.do_action_with_exception(r)
                )
                data = json.loads(res_raw)
                records = data.get("DomainRecords", {}).get("Record", [])
                logger.info(f"[DDNS] 查询 {domain} 共 {len(records)} 条A解析记录")
                return records
            except Exception as e:
                retry -= 1
                logger.warning(f"[DDNS] 查询记录失败，剩余重试{retry}: {e!s}")
                await asyncio.sleep(1)
        logger.error("[DDNS] 查询A记录重试耗尽")
        return None

    async def _delete_record(self, record_id: str) -> bool:
        try:
            req = DeleteDomainRecordRequest.DeleteDomainRecordRequest()
            req.set_RecordId(record_id)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: self.client.do_action_with_exception(req)
            )
            logger.info(f"[DDNS] 清理多余解析记录 ID={record_id}")
            return True
        except Exception as e:
            logger.error(f"[DDNS] 删除记录失败 ID={record_id}: {e!s}")
            return False

    async def _pick_single_a_record(self, records: list[dict]) -> dict | None:
        """只保留目标 RR 的一条记录，删除其余重复记录，避免误删其他解析。"""
        rr = self.config.get_rr().lower()
        target_records = [
            rec for rec in records if str(rec.get("RR", "")).lower() == rr
        ]
        if not target_records:
            return None
        keep_rec = target_records[0]
        for rec in target_records[1:]:
            await self._delete_record(rec["RecordId"])
        return keep_rec

    async def _update_dns_record(self, record_id: str, ip: str) -> bool:
        try:
            rr = self.config.get_rr()
            clean_ip = re.sub(r"\s+", "", ip)
            logger.info(f"[DDNS] 更新解析记录 ID={record_id} RR={rr} IP={clean_ip}")
            req = UpdateDomainRecordRequest.UpdateDomainRecordRequest()
            req.set_RecordId(record_id)
            req.set_RR(rr)
            req.set_Type("A")
            req.set_Value(clean_ip)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: self.client.do_action_with_exception(req)
            )
            logger.info("[DDNS] ✅ 更新解析成功")
            return True
        except Exception as e:
            logger.error(f"[DDNS] ❌ 更新解析失败: {e!s}")
            return False

    async def _add_dns_record(self, ip: str) -> bool:
        try:
            rr = self.config.get_rr()
            clean_ip = re.sub(r"\s+", "", ip)
            logger.info(
                f"[DDNS] 新增解析记录 Domain={self.config.domain} RR={rr} IP={clean_ip}"
            )
            req = AddDomainRecordRequest.AddDomainRecordRequest()
            req.set_DomainName(self.config.domain)
            req.set_RR(rr)
            req.set_Type("A")
            req.set_Value(clean_ip)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: self.client.do_action_with_exception(req)
            )
            logger.info("[DDNS] ✅ 新增解析成功")
            return True
        except Exception as e:
            logger.error(f"[DDNS] ❌ 新增解析失败: {e!s}")
            return False

    async def _check_and_update(self, current_ip: str):
        if not self._is_public_ipv4(current_ip):
            logger.warning(f"[DDNS] 非公网IP，放弃更新: {current_ip}")
            return
        clean_current_ip = re.sub(r"\s+", "", current_ip)

        records = await self._get_all_a_records()
        if records is None:
            logger.error("[DDNS] 查询解析记录失败，跳过本次更新")
            return

        record = await self._pick_single_a_record(records)
        if record:
            raw_dns_ip = record.get("Value", "")
            clean_dns_ip = re.sub(r"\s+", "", raw_dns_ip)
            logger.info(f"[DDNS] DNS云端绑定IP: {clean_dns_ip}")
            if clean_dns_ip == clean_current_ip:
                logger.info(f"[DDNS] 云端IP与公网IP一致，无需操作 {clean_current_ip}")
                return
            logger.info(f"[DDNS] IP变更 {clean_dns_ip} → {clean_current_ip}")
            ok = await self._update_dns_record(record["RecordId"], clean_current_ip)
        else:
            logger.info(f"[DDNS] 无A记录，新建解析 {clean_current_ip}")
            ok = await self._add_dns_record(clean_current_ip)

        if ok:
            self._last_update_time = time.time()
