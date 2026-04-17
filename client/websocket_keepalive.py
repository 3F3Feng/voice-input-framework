#!/usr/bin/env python3
"""
WebSocket 连接保活管理器

实现心跳检测和自动重连机制，解决长时间空闲后连接断开的问题。
"""

import asyncio
import json
import logging
import time
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """连接状态枚举"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class WebSocketKeepAlive:
    """
    WebSocket 连接保活管理器

    功能：
    - 定期发送 ping 消息检测连接状态
    - 检测连接断开后自动重连
    - 提供连接状态回调通知
    """

    # 心跳配置
    DEFAULT_PING_INTERVAL = 30.0  # 每30秒发送一次ping
    DEFAULT_PONG_TIMEOUT = 10.0   # 等待pong响应超时时间
    DEFAULT_MAX_MISSED_PONGS = 3  # 最大允许错过的pong次数
    DEFAULT_RECONNECT_DELAY = 1.0  # 重连延迟（秒）
    DEFAULT_MAX_RECONNECT_DELAY = 30.0  # 最大重连延迟（秒）

    def __init__(
        self,
        ping_interval: float = DEFAULT_PING_INTERVAL,
        pong_timeout: float = DEFAULT_PONG_TIMEOUT,
        max_missed_pongs: int = DEFAULT_MAX_MISSED_PONGS,
        on_state_change: Optional[Callable[[ConnectionState], None]] = None,
        on_reconnect: Optional[Callable[[], asyncio.Coroutine]] = None,
    ):
        """
        初始化保活管理器

        Args:
            ping_interval: ping发送间隔（秒）
            pong_timeout: 等待pong响应超时时间（秒）
            max_missed_pongs: 最大允许错过的pong次数
            on_state_change: 状态变化回调
            on_reconnect: 重连回调（异步函数）
        """
        self.ping_interval = ping_interval
        self.pong_timeout = pong_timeout
        self.max_missed_pongs = max_missed_pongs
        self.on_state_change = on_state_change
        self.on_reconnect = on_reconnect

        # 状态
        self._state = ConnectionState.DISCONNECTED
        self._missed_pongs = 0
        self._last_ping_time = 0
        self._last_pong_time = 0
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # 重连退避
        self._reconnect_delay = self.DEFAULT_RECONNECT_DELAY
        self._max_reconnect_delay = self.DEFAULT_MAX_RECONNECT_DELAY

    @property
    def state(self) -> ConnectionState:
        """当前连接状态"""
        return self._state

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._state == ConnectionState.CONNECTED

    @property
    def last_latency_ms(self) -> float:
        """最后一次ping-pong延迟（毫秒）"""
        if self._last_ping_time > 0 and self._last_pong_time > self._last_ping_time:
            return (self._last_pong_time - self._last_ping_time) * 1000
        return 0

    def _set_state(self, new_state: ConnectionState):
        """设置新状态并触发回调"""
        if self._state != new_state:
            old_state = self._state
            self._state = new_state
            logger.info(f"Connection state changed: {old_state.value} -> {new_state.value}")
            if self.on_state_change:
                try:
                    self.on_state_change(new_state)
                except Exception as e:
                    logger.error(f"State change callback error: {e}")

    async def start(self, websocket_send: Callable[[str], asyncio.Coroutine]):
        """
        启动保活任务

        Args:
            websocket_send: 发送消息的异步函数
        """
        if self._running:
            logger.warning("Keepalive already running")
            return

        self._running = True
        self._websocket_send = websocket_send
        self._set_state(ConnectionState.CONNECTED)
        self._missed_pongs = 0
        self._reconnect_delay = self.DEFAULT_RECONNECT_DELAY

        self._task = asyncio.create_task(self._keepalive_loop())
        logger.info(f"Keepalive started (interval={self.ping_interval}s)")

    async def stop(self):
        """停止保活任务"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._set_state(ConnectionState.DISCONNECTED)
        logger.info("Keepalive stopped")

    async def _keepalive_loop(self):
        """保活主循环"""
        while self._running:
            try:
                await asyncio.sleep(self.ping_interval)

                if not self._running:
                    break

                # 发送ping
                await self._send_ping()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Keepalive loop error: {e}")
                await self._handle_connection_error()

    async def _send_ping(self):
        """发送ping消息并等待pong响应"""
        try:
            self._last_ping_time = time.time()

            # 发送ping
            ping_msg = json.dumps({"type": "ping", "timestamp": self._last_ping_time})
            await self._websocket_send(ping_msg)

            # 等待pong（由 on_pong 方法处理）
            # 如果超时未收到pong，会增加 missed_pongs 计数

        except Exception as e:
            logger.error(f"Send ping failed: {e}")
            await self._handle_connection_error()

    def on_pong(self, timestamp: float):
        """
        处理收到的pong消息

        Args:
            timestamp: ping消息中发送的时间戳
        """
        self._last_pong_time = time.time()
        self._missed_pongs = 0

        # 计算延迟
        latency = (self._last_pong_time - timestamp) * 1000
        logger.debug(f"Pong received, latency: {latency:.0f}ms")

        # 重置重连延迟
        self._reconnect_delay = self.DEFAULT_RECONNECT_DELAY

        # 确保状态为已连接
        if self._state != ConnectionState.CONNECTED:
            self._set_state(ConnectionState.CONNECTED)

    def on_pong_timeout(self):
        """pong超时处理（由外部调用）"""
        self._missed_pongs += 1
        logger.warning(f"Pong timeout (missed: {self._missed_pongs}/{self.max_missed_pongs})")

        if self._missed_pongs >= self.max_missed_pongs:
            logger.error("Max missed pongs reached, connection likely dead")
            asyncio.create_task(self._handle_connection_error())

    async def _handle_connection_error(self):
        """处理连接错误，触发重连"""
        self._set_state(ConnectionState.RECONNECTING)

        # 指数退避重连
        while self._running and self._reconnect_delay <= self._max_reconnect_delay:
            logger.info(f"Attempting to reconnect in {self._reconnect_delay:.1f}s...")
            await asyncio.sleep(self._reconnect_delay)

            if not self._running:
                break

            try:
                if self.on_reconnect:
                    await self.on_reconnect()
                    # 重连成功，重置状态
                    self._missed_pongs = 0
                    self._set_state(ConnectionState.CONNECTED)
                    logger.info("Reconnect successful")
                    return
            except Exception as e:
                logger.error(f"Reconnect attempt failed: {e}")

            # 指数退避
            self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

        # 重连失败
        self._set_state(ConnectionState.ERROR)
        logger.error("Reconnect failed after all attempts")


class ConnectionIndicator:
    """
    连接状态指示器（用于GUI显示）
    """

    STATE_COLORS = {
        ConnectionState.DISCONNECTED: "#888888",  # 灰色
        ConnectionState.CONNECTING: "#ffcc66",    # 黄色
        ConnectionState.CONNECTED: "#66cc66",     # 绿色
        ConnectionState.RECONNECTING: "#ff9944",  # 橙色
        ConnectionState.ERROR: "#ff4444",         # 红色
    }

    STATE_TEXTS = {
        ConnectionState.DISCONNECTED: "未连接",
        ConnectionState.CONNECTING: "连接中...",
        ConnectionState.CONNECTED: "已连接",
        ConnectionState.RECONNECTING: "重连中...",
        ConnectionState.ERROR: "连接错误",
    }

    @classmethod
    def get_color(cls, state: ConnectionState) -> str:
        """获取状态对应的颜色"""
        return cls.STATE_COLORS.get(state, "#888888")

    @classmethod
    def get_text(cls, state: ConnectionState) -> str:
        """获取状态对应的文本"""
        return cls.STATE_TEXTS.get(state, "未知")
