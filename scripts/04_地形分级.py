# -*- coding: utf-8 -*-
"""
脚本04：地形分级 —— 高程分级 / 坡度分级 / 地形位指数(TPI)
纯重分类计算, 无经验参数争议
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['PROJ_LIB'] = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'Lib', 'site-packages', 'rasterio', 'proj_data')

import numpy as np
import rasterio
from scipy.ndimage import uniform_filter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT = os.path.join(BASE_DIR, '成果数据')

dem_path = os.path.join(BASE_DIR, '处理中', 'dem_filled.tif')
slope_path = os.path.join(RESULT, 'slope.tif')

print('=' * 60)
print('  脚本04: 地形分级')
print('=' * 60)

# 读数据
with rasterio.open(dem_path) as src:
    dem = src.read(1).astype('float32')
    meta = src.meta.copy()
    valid = dem > -9000

with rasterio.open(slope_path) as src:
    slope = src.read(1).astype('float32')
    valid_s = slope > -9000

valid_all = valid & valid_s

# ============================================================
# 1. 高程分级 (国家标准)
# ============================================================
print(f'\n[1] 高程分级...')

# 中国1:100万地貌图制图规范 高程分级
elev_zones = {
    1: (0, 50, '平原'),
    2: (50, 100, '低平原/台地'),
    3: (100, 200, '丘陵'),
    4: (200, 500, '低山'),
    5: (500, 1000, '中山'),
    6: (1000, 3500, '高中山'),
}

elev_class = np.full_like(dem, 255, dtype='uint8')
for code, (lo, hi, label) in elev_zones.items():
    mask = (dem >= lo) & (dem < hi) & valid
    elev_class[mask] = code
    cnt = mask.sum()
    pct = cnt / valid.sum() * 100
    print(f'  {code}: {label} [{lo}-{hi}m)  {cnt:>10,} px ({pct:.1f}%)')

out_meta = meta.copy()
out_meta.update(dtype='uint8', nodata=255, compress='lzw')
elev_path = os.path.join(RESULT, 'elevation_zones.tif')
with rasterio.open(elev_path, 'w', **out_meta) as dst:
    dst.write(elev_class, 1)
print(f'  [OK] elevation_zones.tif')

# ============================================================
# 2. 坡度分级 (水土保持规范)
# ============================================================
print(f'\n[2] 坡度分级...')

slope_zones = {
    1: (0, 2, '平坦'),
    2: (2, 5, '微坡'),
    3: (5, 15, '缓坡'),
    4: (15, 25, '中坡'),
    5: (25, 45, '陡坡'),
    6: (45, 90, '极陡坡'),
}

slope_class = np.full_like(slope, 255, dtype='uint8')
for code, (lo, hi, label) in slope_zones.items():
    mask = (slope >= lo) & (slope < hi) & valid_all
    slope_class[mask] = code
    cnt = mask.sum()
    pct = cnt / valid_all.sum() * 100
    print(f'  {code}: {label} [{lo}-{hi}deg)  {cnt:>10,} px ({pct:.1f}%)')

slope_class_path = os.path.join(RESULT, 'slope_zones.tif')
with rasterio.open(slope_class_path, 'w', **out_meta) as dst:
    dst.write(slope_class, 1)
print(f'  [OK] slope_zones.tif')

# ============================================================
# 3. 地形位指数 TPI (Weiss 2001)
#    TPI = elev - mean(elev in neighborhood)
#    TPI > 0: 山脊/凸起, TPI < 0: 沟谷/凹陷
# ============================================================
print(f'\n[3] 地形位指数 (TPI, 300m邻域)...')

# 300m 邻域 ≈ 10 像素 (30m分辨率)
radius = 10
size = radius * 2 + 1
neighborhood_mean = uniform_filter(np.where(valid, dem, 0), size=size)
neighborhood_count = uniform_filter(valid.astype('float32'), size=size)
neighborhood_mean = np.where(neighborhood_count > 0,
                              neighborhood_mean / neighborhood_count, -9999)

tpi = np.full_like(dem, np.nan, dtype='float32')
tpi[valid] = dem[valid] - neighborhood_mean[valid]

# TPI 分级 (Weiss 分类)
tpi_class = np.full_like(dem, 255, dtype='uint8')
tpi_valid = valid & (~np.isnan(tpi))
tpi_v = tpi[tpi_valid]
tpi_std = np.nanstd(tpi_v)

tpi_zones = {
    1: (-1000, -2*tpi_std, '深谷'),
    2: (-2*tpi_std, -1*tpi_std, '谷地'),
    3: (-1*tpi_std, -0.5*tpi_std, '浅谷'),
    4: (-0.5*tpi_std, 0.5*tpi_std, '平地/缓坡'),
    5: (0.5*tpi_std, 1*tpi_std, '山脊'),
    6: (1*tpi_std, 2*tpi_std, '高脊'),
    7: (2*tpi_std, 5000, '峰顶/陡崖'),
}

for code, (lo, hi, label) in tpi_zones.items():
    mask = (tpi >= lo) & (tpi < hi) & tpi_valid
    tpi_class[mask] = code
    print(f'  {code}: {label} [{lo:.1f}~{hi:.1f}]  {mask.sum():>10,} px')

out_meta_f32 = meta.copy()
out_meta_f32.update(dtype='float32', nodata=-9999, compress='lzw')
tpi_path = os.path.join(RESULT, 'tpi.tif')
with rasterio.open(tpi_path, 'w', **out_meta_f32) as dst:
    dst.write(np.where(np.isnan(tpi), -9999, tpi).astype('float32'), 1)

tpi_class_path = os.path.join(RESULT, 'tpi_zones.tif')
with rasterio.open(tpi_class_path, 'w', **out_meta) as dst:
    dst.write(tpi_class, 1)
print(f'  [OK] tpi.tif + tpi_zones.tif')

print(f'\n{"=" * 60}')
print(f'  地形分级完成 → {RESULT}/')
print(f'{"=" * 60}')
