"""独立 WPS Channel 长连接进程入口。"""

import asyncio

from yuxi.services.wps_channel_runtime import WPSChannelRuntime


if __name__ == "__main__":
    asyncio.run(WPSChannelRuntime().run())
