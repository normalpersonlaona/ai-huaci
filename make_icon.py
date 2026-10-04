"""
生成托盘图标 —— 纯 Python 手写 ICO 字节，不依赖 Pillow。

图形：蓝底圆角方块 + 两条白色文字条（一条长一条短），
      意思是"被划选中的两行字"。

跑一次就够：  python make_icon.py
"""

import struct
import os

BLUE = (47, 111, 235)      # #2f6feb
WHITE = (255, 255, 255)


def _rounded_rect_alpha(x, y, w, h, r, px, py):
    """圆角矩形命中测试，返回 True/False。"""
    if px < x or py < y or px >= x + w or py >= y + h:
        return False
    # 四角
    for cx, cy in ((x + r, y + r), (x + w - r - 1, y + r),
                   (x + r, y + h - r - 1), (x + w - r - 1, y + h - r - 1)):
        inside_x = (px < x + r) if cx == x + r else (px > x + w - r - 1)
        inside_y = (py < y + r) if cy == y + r else (py > y + h - r - 1)
        if inside_x and inside_y:
            dx, dy = px - cx, py - cy
            return dx * dx + dy * dy <= r * r
    return True


def render(size):
    """
    返回 size x size 的像素数组，每个元素是 (B, G, R, A) —— 注意 ICO 是 BGRA 序。
    """
    ss = 4                      # 超采样倍数，边缘才不毛糙
    S = size * ss
    px = [[(0, 0, 0, 0)] * size for _ in range(size)]

    pad = max(1, S // 16)
    rad = max(2, int(S * 0.22))
    box_w = S - pad * 2

    bar_h = max(2, int(S * 0.11))
    bar_r = bar_h // 2
    bar1_y = int(S * 0.30)
    bar2_y = int(S * 0.56)

    for y in range(size):
        for x in range(size):
            hits = {'bg': 0, 'b1': 0, 'b2': 0}
            for sy in range(ss):
                for sx in range(ss):
                    fx = x * ss + sx
                    fy = y * ss + sy
                    if not _rounded_rect_alpha(pad, pad, box_w, box_w, rad, fx, fy):
                        continue
                    hits['bg'] += 1
                    # 第一条：占 66%
                    if _rounded_rect_alpha(pad + int(box_w * 0.17), bar1_y,
                                           int(box_w * 0.66), bar_h, bar_r, fx, fy):
                        hits['b1'] += 1
                    # 第二条：占 42%
                    if _rounded_rect_alpha(pad + int(box_w * 0.17), bar2_y,
                                           int(box_w * 0.42), bar_h, bar_r, fx, fy):
                        hits['b2'] += 1
            total = ss * ss
            if hits['bg'] == 0:
                continue
            bg_a = hits['bg'] / total
            if hits['b1'] >= hits['bg'] * 0.5 or hits['b2'] >= hits['bg'] * 0.5:
                r, g, b = WHITE
            else:
                r, g, b = BLUE
            a = int(255 * bg_a)
            px[y][x] = (b, g, r, a)
    return px


def ico_bytes(sizes=(16, 32, 48)):
    images = []
    for s in sizes:
        px = render(s)
        # BITMAPINFOHEADER：高度写两倍（XOR + AND）
        bmp_header = struct.pack('<IiiHHIIiiII',
                                 40, s, s * 2, 1, 32, 0, 0, 0, 0, 0, 0)
        # XOR：BGRA，自下而上
        xor = bytearray()
        for y in range(s - 1, -1, -1):
            for x in range(s):
                b, g, r, a = px[y][x]
                xor += bytes((b, g, r, a))
        # AND 掩码：32bpp 下用不到，但格式要求有，全 0 即可
        row_bytes = ((s + 31) // 32) * 4
        and_mask = bytes(row_bytes * s)
        images.append(bmp_header + bytes(xor) + and_mask)

    out = bytearray()
    out += struct.pack('<HHH', 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for s, data in zip(sizes, images):
        out += struct.pack('<BBBBHHII',
                           s if s < 256 else 0, s if s < 256 else 0,
                           0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for data in images:
        out += data
    return bytes(out)


if __name__ == '__main__':
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
    with open(path, 'wb') as f:
        f.write(ico_bytes())
    print('图标已生成:', path, os.path.getsize(path), 'bytes')
