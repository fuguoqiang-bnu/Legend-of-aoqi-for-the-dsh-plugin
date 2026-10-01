"""把 docs/skins-preview.png 里内置网页那一块裁出来当 docs/find-panel.png。

为什么要裁而不是单独拍一张：只渲染网页面板时，无头 Chromium 的 --screenshot 会拍到
iframe 还没画完的一帧（同一份组件、同一个 src，和皮肤中心一起渲染时就能拍到）。
所以文档图统一用「整页渲染 + 裁剪」，保证图和实拍一致、也不是拼的。
"""
import sys
from PIL import Image

src, dst, box = sys.argv[1], sys.argv[2], [int(v) for v in sys.argv[3:7]]
image = Image.open(src)
print('source', image.size)
crop = image.crop(tuple(box))
crop.save(dst)
print('wrote', dst, crop.size)
