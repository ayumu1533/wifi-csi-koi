"""全自動収集（collection.plan）の音声案内: 読み上げ（Speaker）とビープ音（Beeper）。Windows専用（他のOSでは無音）。"""
from __future__ import annotations

import base64
import logging
import os
import subprocess
import threading
import time

logger = logging.getLogger("collect_plan")


class Speaker:
    """Windowsの音声合成（System.Speech）で読み上げる。PowerShellを1つ起動したままにして使い回す。"""

    _SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $s.SelectVoiceByHints('NotSet', 'NotSet', 0, [Globalization.CultureInfo]'ja-JP') } catch {}
$s.Rate = 1
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($line -eq $null) { break }
  $s.Speak([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($line)))
  [Console]::Out.WriteLine('done')
}
"""

    def __init__(self, enabled: bool) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        if not enabled or os.name != "nt":
            return
        encoded = base64.b64encode(self._SCRIPT.encode("utf-16-le")).decode("ascii")
        try:
            self._proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
        except OSError as exc:
            logger.warning("音声合成を起動できません（読み上げなしで続けます）: %s", exc)

    def say(self, text: str) -> None:
        logger.info("[読み上げ] %s", text)
        if self._proc is None:
            return
        with self._lock:
            try:
                self._proc.stdin.write(base64.b64encode(text.encode("utf-8")).decode("ascii") + "\n")
                self._proc.stdin.flush()
                self._proc.stdout.readline()  # 読み終わりの "done" を待つ
            except (OSError, ValueError) as exc:
                logger.warning("読み上げに失敗しました（以降は読み上げなし）: %s", exc)
                self._proc = None

    def close(self) -> None:
        if self._proc is not None:
            self._proc.stdin.close()
            self._proc.wait(timeout=5)


class Beeper:
    def __init__(self, enabled: bool) -> None:
        self._winsound = None
        if enabled and os.name == "nt":
            import winsound
            self._winsound = winsound

    def _beep(self, *tones: tuple[int, int]) -> None:
        if self._winsound is None:
            return
        for freq, ms in tones:
            if freq == 0:
                time.sleep(ms / 1000)  # 無音の間
            else:
                self._winsound.Beep(freq, ms)

    def tick(self) -> None:
        self._beep((880, 150))

    def start(self) -> None:
        self._beep((1320, 700))

    def end(self) -> None:
        self._beep((1320, 150), (0, 80), (1320, 150))

    def error(self) -> None:
        self._beep((300, 900))
