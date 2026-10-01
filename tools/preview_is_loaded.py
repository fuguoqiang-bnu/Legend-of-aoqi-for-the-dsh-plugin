"""判断截图里「内置网页」那块到底拍到了真站点没有。

无头 Chromium 的 --screenshot 有时会在 iframe 还没画完时就把帧拍下来（同一份组件、
同一个 src，快慢不定）。dshfind 的顶栏是深色的，没拍到时那块是皮肤底图（很亮），
所以用一小条区域的亮度当判据：暗 = 拍到了站点，亮 = 还是底图。

用法：python tools/preview_is_loaded.py <png> <x1> <y1> <x2> <y2>
退出码 0 = 拍到了；1 = 没拍到（亮度 >= 阈值）。
"""
import sys
from PIL import Image

path = sys.argv[1]
box = tuple(int(v) for v in sys.argv[2:6])
THRESHOLD = 105.0

strip = Image.open(path).convert('L').crop(box)
pixels = list(strip.getdata())
mean = sum(pixels) / len(pixels)
dark_ratio = sum(1 for p in pixels if p < 90) / len(pixels)
print(f'mean_luma={mean:.1f} dark_ratio={dark_ratio:.2f} box={box}')
sys.exit(0 if mean < THRESHOLD else 1)
