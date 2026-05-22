# -*- coding: utf-8 -*-
"""
脚本03：地形分析 —— 坡度/坡向/山体阴影/地形起伏度/等高线
Horn (1981) 算法, 确定性计算
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['PROJ_LIB'] = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'Lib', 'site-packages', 'rasterio', 'proj_data')

import numpy as np
import rasterio
import geopandas as gpd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROCESSING = os.path.join(BASE_DIR, '处理中')
RESULT = os.path.join(BASE_DIR, '成果数据')
os.makedirs(RESULT, exist_ok=True)

TARGET_CRS = 'EPSG:4527'

dem_path = os.path.join(PROCESSING, 'dem_filled.tif')

print('=' * 60)
print('  脚本03: 地形分析')
print('=' * 60)

with rasterio.open(dem_path) as src:
    dem = src.read(1).astype('float32')
    transform = src.transform
    meta = src.meta.copy()
    dx = abs(transform.a)
    dy = abs(transform.e)
    valid = dem > -9000

dem_f = np.where(valid, dem, np.nan)
print(f'  输入: {dem.shape[1]}x{dem.shape[0]}, CRS={meta["crs"]}, 分辨率={dx:.0f}m')

# ============================================================
# 1. Horn 1981 坡度 + 坡向
# ============================================================
print(f'\n[1] 坡度 + 坡向 (Horn 1981)...')

z = dem_f
dzdx = np.zeros_like(z)
dzdy = np.zeros_like(z)
inner = np.s_[1:-1, 1:-1]

# dz/dx = ((c + 2f + i) - (a + 2d + g)) / (8*dx)    3x3: a b c / d _ f / g h i
dzdx[inner] = ((z[:-2, 2:] + 2*z[1:-1, 2:] + z[2:, 2:]) -
               (z[:-2, :-2] + 2*z[1:-1, :-2] + z[2:, :-2])) / (8 * dx)
# dz/dy = ((g + 2h + i) - (a + 2b + c)) / (8*dy)
dzdy[inner] = ((z[2:, :-2] + 2*z[2:, 1:-1] + z[2:, 2:]) -
               (z[:-2, :-2] + 2*z[:-2, 1:-1] + z[:-2, 2:])) / (8 * dy)

rise_run = np.sqrt(dzdx**2 + dzdy**2)
slope = np.full_like(z, np.nan, dtype='float32')
aspect = np.full_like(z, np.nan, dtype='float32')
slope[1:-1, 1:-1] = np.arctan(rise_run[1:-1, 1:-1]) * 180 / np.pi
aspect[1:-1, 1:-1] = (np.arctan2(dzdy[1:-1, 1:-1], -dzdx[1:-1, 1:-1]) * 180 / np.pi) % 360
aspect[(slope < 0.1) & valid] = -1  # 平坦区域

# ============================================================
# 2. 山体阴影
# ============================================================
print(f'[2] 山体阴影 (az=315 alt=45)...')
azimuth, altitude = 315.0, 45.0
zenith_rad = np.radians(90 - altitude)
azimuth_rad = np.radians(360 - azimuth)
slope_rad = np.radians(np.clip(slope, 0, 89))
aspect_rad = np.radians(np.where(aspect >= 0, aspect, 0))

hs = (np.cos(zenith_rad) * np.cos(slope_rad) +
      np.sin(zenith_rad) * np.sin(slope_rad) *
      np.cos(azimuth_rad - aspect_rad))
hillshade = np.clip(hs * 255, 0, 255).astype('uint8')
hillshade[~valid] = 0

# ============================================================
# 3. 地形起伏度 TRI
# ============================================================
print(f'[3] 地形起伏度 (Riley TRI)...')
from scipy.ndimage import uniform_filter
tri_sq = np.where(np.isfinite(rise_run), rise_run**2, 0)
tri_mask = np.isfinite(rise_run).astype('float32')
tri_sum = uniform_filter(tri_sq, size=3)
tri_cnt = uniform_filter(tri_mask, size=3)
tri = np.full_like(rise_run, np.nan, dtype='float32')
inner = np.s_[1:-1, 1:-1]
tri[inner] = np.sqrt(tri_sum[inner] / np.maximum(tri_cnt[inner], 1))
tri[~valid] = np.nan

# ============================================================
# 4. 保存栅格
# ============================================================
print(f'[4] 保存结果...')
out_meta = meta.copy()
out_meta.update(compress='lzw', dtype='float32', nodata=-9999)

for name, arr in [('slope', slope), ('aspect', aspect), ('tri', tri)]:
    arr_save = np.where(np.isnan(arr), -9999, arr)
    path = os.path.join(RESULT, f'{name}.tif')
    with rasterio.open(path, 'w', **out_meta) as dst:
        dst.write(arr_save.astype('float32'), 1)
    print(f'  [OK] {name}.tif')

out_meta_hs = meta.copy()
out_meta_hs.update(compress='lzw', dtype='uint8', nodata=0)
hs_path = os.path.join(RESULT, 'hillshade.tif')
with rasterio.open(hs_path, 'w', **out_meta_hs) as dst:
    dst.write(hillshade, 1)
print(f'  [OK] hillshade.tif')

# ============================================================
# 5. 等高线 (GDAL Contour)
# ============================================================
print(f'[5] 等高线 (间距 50m)...')
try:
    from osgeo import gdal, ogr, osr as gdal_osr
    dem_ds = gdal.Open(dem_path)
    band = dem_ds.GetRasterBand(1)

    contour_path = os.path.join(RESULT, 'contours_50m.shp')
    drv = ogr.GetDriverByName('ESRI Shapefile')
    if os.path.exists(contour_path):
        for f in os.listdir(os.path.dirname(contour_path)):
            if 'contours_50m' in f:
                os.remove(os.path.join(os.path.dirname(contour_path), f))

    ds_out = drv.CreateDataSource(contour_path)
    srs = gdal_osr.SpatialReference()
    srs.ImportFromEPSG(4527)
    layer = ds_out.CreateLayer('contours', srs, ogr.wkbLineString)
    field = ogr.FieldDefn('elev', ogr.OFTInteger)
    layer.CreateField(field)

    gdal.ContourGenerate(band, 50, 0, [], 0, 0, layer, 0, 1)
    ds_out = None
    dem_ds = None

    contours_gdf = gpd.read_file(contour_path)
    print(f'  等高线: {len(contours_gdf)} 条')
    print(f'  高程范围: {contours_gdf["elev"].min():.0f} ~ {contours_gdf["elev"].max():.0f} m')
    print(f'  [OK] contours_50m.shp')
except ImportError:
    print('  GDAL bindings 不可用, 跳过等高线')
except Exception as e:
    print(f'  等高线失败: {e}')

# ============================================================
# 6. 统计
# ============================================================
print(f'\n[6] 统计摘要...')
sv = slope[1:-1, 1:-1]
av = aspect[1:-1, 1:-1]
tv = tri[1:-1, 1:-1]
ev = dem_f[valid]

print(f'  高程: min={ev.min():.0f}  max={ev.max():.0f}  mean={ev.mean():.0f}  std={ev.std():.0f}m')
print(f'  坡度: mean={np.nanmean(sv):.1f}deg  median={np.nanmedian(sv):.1f}deg  '
      f'max={np.nanmax(sv):.1f}deg')
print(f'  坡向: 平坦占比={np.sum(aspect[valid]==-1)/valid.sum()*100:.1f}%')
print(f'  起伏度: mean={np.nanmean(tv):.3f}  max={np.nanmax(tv):.3f}')

print(f'\n{"=" * 60}')
print(f'  地形分析完成 → {RESULT}/')
print(f'{"=" * 60}')
