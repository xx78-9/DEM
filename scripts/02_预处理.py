# -*- coding: utf-8 -*-
"""
脚本02：预处理 —— 重投影(CGCS2000)、裁剪、填洼、标准化
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 修复 PostgreSQL PROJ 冲突
os.environ['PROJ_LIB'] = os.path.join(
    os.path.dirname(os.path.abspath(sys.executable)), 'Lib', 'site-packages',
    'rasterio', 'proj_data')

import numpy as np
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(BASE_DIR, '原始数据')
PROCESSING = os.path.join(BASE_DIR, '处理中')
os.makedirs(PROCESSING, exist_ok=True)

TARGET_CRS = 'EPSG:4527'  # CGCS2000 / 3-degree Gauss-Kruger CM 120E
TARGET_RES = 30.0          # 30m

# ============================================================
# 1. 重投影: WGS84(geographic) → CGCS2000(projected)
# ============================================================
print('=' * 60)
print('  脚本02: 预处理 - 重投影 + 填洼 + 标准化')
print('=' * 60)

dem_raw = os.path.join(RAW, 'hangzhou_dem_full.tif')
dem_reproj = os.path.join(PROCESSING, 'dem_reprojected.tif')

print(f'\n[1] 重投影到 {TARGET_CRS}...')

with rasterio.open(dem_raw) as src:
    transform, width, height = calculate_default_transform(
        src.crs, TARGET_CRS, src.width, src.height,
        *src.bounds, resolution=TARGET_RES
    )
    kwargs = src.meta.copy()
    kwargs.update(crs=TARGET_CRS, transform=transform,
                  width=width, height=height, compress='lzw')

    with rasterio.open(dem_reproj, 'w', **kwargs) as dst:
        for i in range(1, src.count + 1):
            reproject(
                source=rasterio.band(src, i),
                destination=rasterio.band(dst, i),
                src_transform=src.transform, src_crs=src.crs,
                dst_transform=transform, dst_crs=TARGET_CRS,
                resampling=Resampling.bilinear
            )

with rasterio.open(dem_reproj) as src:
    data = src.read(1)
    valid = data > -9000
    print(f'  重投影后: {src.width}x{src.height}')
    print(f'  CRS: {src.crs}')
    print(f'  分辨率: {src.res}')
    print(f'  有效像素: {valid.sum():,}')
    print(f'  高程: {data[valid].min():.0f} ~ {data[valid].max():.0f} m')

# ============================================================
# 2. 填洼 - 填补 DEM 中的局部凹陷
#    使用 rasterio.fill.fillnodata + 简单形态学处理
# ============================================================
print(f'\n[2] 填洼处理...')

dem_filled = os.path.join(PROCESSING, 'dem_filled.tif')
with rasterio.open(dem_reproj) as src:
    dem = src.read(1).astype('float32')
    meta = src.meta.copy()

# 将有效数据分离
valid_mask = dem > -9000
dem_masked = np.where(valid_mask, dem, np.nan)

# 简单填洼: 使用 interpolation-based fill + 微小凹陷修正
# 对局部洼地: 如果像素比周围8邻域均值低, 提升到邻域中值
from scipy.ndimage import median_filter, uniform_filter

filled = dem_masked.copy()
# 迭代2次, 仅处理小凹陷 (大凹陷可能是真实地形如喀斯特)
for _ in range(2):
    med = median_filter(np.nan_to_num(filled, nan=-9999), size=3)
    mean = uniform_filter(np.nan_to_num(filled, nan=-9999), size=3)
    # 洼地: 像素值低于邻域中值
    sink = (filled < med - 1.0) & valid_mask
    filled[sink] = med[sink]
    print(f'  填洼像素: {sink.sum():,}')

filled[~valid_mask] = -9999

meta.update(compress='lzw')
with rasterio.open(dem_filled, 'w', **meta) as dst:
    dst.write(filled.astype('float32'), 1)

n_filled = (np.abs(filled - dem_masked) > 0.1).sum()
print(f'  修正像素总数: {n_filled:,} ({n_filled/valid_mask.sum()*100:.2f}%)')
print(f'  输出: {dem_filled}')

# ============================================================
# 3. 生成快速预览统计
# ============================================================
print(f'\n[3] 统计摘要...')
v = filled[valid_mask]
stats = {'min': float(v.min()), 'max': float(v.max()),
         'mean': float(v.mean()), 'std': float(v.std()),
         'p5': float(np.percentile(v, 5)),
         'p50': float(np.percentile(v, 50)),
         'p95': float(np.percentile(v, 95))}
for k, val in stats.items():
    print(f'  {k}: {val:.1f} m')

print(f'\n{"=" * 60}')
print(f'  预处理完成 → {PROCESSING}/')
print(f'{"=" * 60}')
