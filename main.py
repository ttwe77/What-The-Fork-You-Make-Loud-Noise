# -*- coding: utf-8 -*-
"""
监听麦克风响度，超过阈值时自动调整系统音量（Windows）

依赖安装：
    pip install sounddevice numpy pycaw comtypes

    Linux 还需要：sudo apt install libportaudio2
"""

import time
import numpy as np
import sounddevice as sd

# ==================== 配置区 ====================
THRESHOLD_DB  = -35.0    # 触发阈值（dBFS）：0 为最大，越负越安静。-30 约等于正常说话
TARGET_VOLUME = 0.5     # 触发后把系统音量设为 25%（0.0 ~ 1.0）
RESTORE       = True     # 安静一段时间后，是否恢复原来的音量
RESTORE_AFTER = 3.0      # 安静多少秒后恢复（秒）
SAMPLE_RATE   = 48000    # 采样率
BLOCK_SIZE    = 1024     # 每块采样点数（约 21ms）
COOLDOWN      = 1.0      # 两次触发的最小间隔（秒），防止抖动
SMOOTH        = 0.7      # 响度平滑系数（0~1，越大越平滑、反应越慢）
DEBUG         = False    # True 时只打印响度、不调音量（用于校准阈值）
# ===============================================


# ---------------- 系统音量控制（Windows） ----------------
class WindowsVolume:
    """封装 pycaw，兼容新旧版本 API"""

    def __init__(self):
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        speakers = AudioUtilities.GetSpeakers()
        try:
            # pycaw >= 2023 的新 API
            self._vol = speakers.EndpointVolume
        except AttributeError:
            # 旧版 API
            interface = speakers.Activate(
                IAudioEndpointVolume._iid_, CLSCTX_ALL, None
            )
            self._vol = cast(interface, POINTER(IAudioEndpointVolume))

    def get(self) -> float:
        """获取当前主音量 0.0 ~ 1.0"""
        return self._vol.GetMasterVolumeLevelScalar()

    def set(self, value: float) -> None:
        """设置主音量 0.0 ~ 1.0"""
        v = float(np.clip(value, 0.0, 1.0))
        self._vol.SetMasterVolumeLevelScalar(v, None)

    def mute(self, flag: bool = True) -> None:
        self._vol.SetMute(1 if flag else 0, None)


# 如果不用 Windows，可以换成下面这个 Linux 版本：
#
# import subprocess
# class LinuxVolume:
#     def get(self):
#         out = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
#                              capture_output=True, text=True).stdout
#         return int(out.split("/")[1].strip().rstrip("%")) / 100
#     def set(self, v):
#         subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@",
#                         f"{int(v * 100)}%"])


# ---------------- 响度计算 ----------------
def rms_to_db(block: np.ndarray) -> float:
    """把一段音频转换成 dBFS 响度"""
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    return 20.0 * np.log10(rms + 1e-12)


# ---------------- 主程序 ----------------
def main():
    vol = WindowsVolume()

    level_db = -np.inf          # 平滑后的响度
    last_trigger = 0.0          # 上次触发时间
    saved_volume = None         # 触发前的原始音量
    quiet_since = None          # 开始安静的时刻

    print(f"开始监听麦克风…… 阈值 = {THRESHOLD_DB} dB，Ctrl+C 退出\n")

    with sd.InputStream(
        channels=1,
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        dtype="float32",
    ) as stream:
        while True:
            block, overflowed = stream.read(BLOCK_SIZE)
            db = rms_to_db(block[:, 0])

            # 指数平滑，避免单个尖峰误触发
            level_db = (
                SMOOTH * level_db + (1 - SMOOTH) * db
                if np.isfinite(level_db) else db
            )

            now = time.time()

            if DEBUG:
                print(f"\r响度: {level_db:7.1f} dBFS", end="", flush=True)

            # ---- 超过阈值：调低音量 ----
            if level_db > THRESHOLD_DB and (now - last_trigger) >= COOLDOWN:
                last_trigger = now
                if saved_volume is None:          # 只记录一次原始音量
                    saved_volume = vol.get()
                if not DEBUG:
                    vol.set(TARGET_VOLUME)
                quiet_since = None
                print(f"\r[{time.strftime('%H:%M:%S')}] 响度 {level_db:6.1f} dB "
                      f"→ 音量设为 {TARGET_VOLUME:.0%}")

            # ---- 安静下来：恢复音量 ----
            elif RESTORE and saved_volume is not None:
                if level_db <= THRESHOLD_DB:
                    if quiet_since is None:
                        quiet_since = now
                    elif now - quiet_since >= RESTORE_AFTER:
                        if not DEBUG:
                            vol.set(saved_volume)
                        print(f"\r[{time.strftime('%H:%M:%S')}] 安静 {RESTORE_AFTER:.0f}s "
                              f"→ 恢复音量 {saved_volume:.0%}")
                        saved_volume = None
                        quiet_since = None
                else:
                    quiet_since = None


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出")