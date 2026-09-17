"""Transport fixtures only: no real images/voice and no OCR/STT accuracy claim."""
from pathlib import Path
import math
import struct
import wave
from PIL import Image, ImageDraw


def write_synthetic_media(path: Path, code: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".wav":
        rate = 8000
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(b"".join(
                struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / rate)))
                for i in range(1600)
            ))
        return
    picture = Image.new("RGB", (160, 100), "white")
    ImageDraw.Draw(picture).text((10, 20), f"SYNTHETIC FIXTURE {code:04d}", fill="black")
    picture.save(path)
