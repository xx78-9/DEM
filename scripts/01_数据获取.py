# -*- coding: utf-8 -*-
"""
脚本01：获取数据 —— OSM杭州边界 + Copernicus DEM 30m 下载/拼接/裁剪

数据源:
  - 行政区划: OpenStreetMap (osmnx 在线获取)
  - DEM: ESA Copernicus DEM GLO-30 (AWS Open Data, 免费免注册)
  裁剪使用杭州行政边界精确多边形, 不用矩形框。
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['PROJ_LIB'] = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'Lib', 'site-packages', 'rasterio', 'proj_data')

import geopandas as gpd
import numpy as np
import rasterio
import rasterio.mask
import rasterio.merge
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import osmnx as ox

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(BASE_DIR, '原始数据')
CACHE = os.path.join(RAW, '_cache')
os.makedirs(CACHE, exist_ok=True)

# Copernicus DEM 瓦片 (1x1 tile): 杭州覆盖 6 片
DEM_TILES = [
    'Copernicus_DSM_COG_10_N29_00_E118_00_DEM',
    'Copernicus_DSM_COG_10_N29_00_E119_00_DEM',
    'Copernicus_DSM_COG_10_N29_00_E120_00_DEM',
    'Copernicus_DSM_COG_10_N30_00_E118_00_DEM',
    'Copernicus_DSM_COG_10_N30_00_E119_00_DEM',
    'Copernicus_DSM_COG_10_N30_00_E120_00_DEM',
]
AWS_BASE = 'https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com'


def download_with_resume(url, dest_path, max_retries=5):
    """断点续传下载"""
    tmp = dest_path + '.tmp'
    existing = 0
    if os.path.exists(tmp):
        existing = os.path.getsize(tmp)
    elif os.path.exists(dest_path):
        return True

    for attempt in range(max_retries):
        try:
            headers = {'Range': f'bytes={existing}-'} if existing > 0 else {}
            session = requests.Session()
            retry = Retry(total=2, backoff_factor=2,
                          status_forcelist=[429, 500, 502, 503, 504])
            session.mount('https://', HTTPAdapter(max_retries=retry))
            resp = session.get(url, headers=headers, stream=True, timeout=(30, 600))
            mode = 'ab' if resp.status_code == 206 else 'wb'
            if mode == 'wb':
                existing = 0

            with open(tmp, mode) as f:
                for chunk in resp.iter_content(chunk_size=4194304):
                    if chunk:
                        f.write(chunk)

            if 'Content-Length' in resp.headers and resp.status_code == 200:
                expected = int(resp.headers['Content-Length'])
                if os.path.getsize(tmp) < expected * 0.99:
                    existing = os.path.getsize(tmp)
                    continue

            os.rename(tmp, dest_path)
            return True
        except Exception as e:
            existing = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            wait = 2 ** attempt
            print(f'  重试 {attempt+1}/{max_retries} (等{wait}s): {e}')
            time.sleep(wait)
    return False


# ============================================================
# Step 1: 获取杭州行政区划边界
# ============================================================
print('=' * 60)
print('  Step 1: 获取杭州行政区划 (OSM)')
print('=' * 60)

boundary_path = os.path.join(RAW, 'hangzhou_boundary.geojson')

if os.path.exists(boundary_path):
    hangzhou = gpd.read_file(boundary_path)
    print(f'  已有边界: {len(hangzhou)} 要素')
else:
    print('  从 OSM 下载杭州边界...')
    gdf = ox.geocode_to_gdf('Hangzhou, Zhejiang, China')
    gdf.to_json(boundary_path, force_ascii=False)
    hangzhou = gpd.read_file(boundary_path)
    print(f'  已保存: {boundary_path}')

hangzhou_4326 = hangzhou.to_crs('EPSG:4326')
geom_wgs84 = hangzhou_4326.geometry.union_all()
print(f'  BBox: {hangzhou_4326.total_bounds.tolist()}')

# ============================================================
# Step 2: 下载 DEM 瓦片 (断点续传)
# ============================================================
print(f'\n{"=" * 60}')
print('  Step 2: 下载 Copernicus DEM 30m 瓦片')
print('=' * 60)

for i, tile_name in enumerate(DEM_TILES):
    cache_path = os.path.join(CACHE, f'{tile_name}.tif')
    print(f'\n  [{i+1}/6] {tile_name}')

    if os.path.exists(cache_path):
        mb = os.path.getsize(cache_path) / 1024 / 1024
        if mb > 10:
            print(f'    [缓存] {mb:.0f} MB, 跳过')
            continue
        else:
            os.remove(cache_path)
            print(f'    [缓存太小 {mb:.1f}MB], 重新下载')

    url = f'{AWS_BASE}/{tile_name}/{tile_name}.tif'
    total_mb = 0
    try:
        resp = requests.head(url, timeout=30)
        total_mb = int(resp.headers.get('Content-Length', 0)) / 1024 / 1024
    except Exception:
        pass
    print(f'    大小: ~{total_mb:.0f} MB, 下载中...', end=' ', flush=True)

    t0 = time.time()
    if download_with_resume(url, cache_path):
        elapsed = time.time() - t0
        mb = os.path.getsize(cache_path) / 1024 / 1024
        print(f'{mb:.0f}MB ({elapsed:.0f}s) [OK]')
    else:
        print('[FAIL]')
        raise RuntimeError(f'下载失败: {tile_name}')

# ============================================================
# Step 3: 拼接瓦片
# ============================================================
print(f'\n{"=" * 60}')
print('  Step 3: 拼接 4 瓦片')
print('=' * 60)

mosaic_path = os.path.join(CACHE, '_hangzhou_mosaic.tif')
if not os.path.exists(mosaic_path):
    datasets = [rasterio.open(os.path.join(CACHE, f'{t}.tif')) for t in DEM_TILES]
    print(f'  输入: {len(datasets)} 个瓦片')
    mosaic, out_transform = rasterio.merge.merge(datasets, method='first')
    out_meta = datasets[0].meta.copy()
    out_meta.update(height=mosaic.shape[1], width=mosaic.shape[2],
                    transform=out_transform, compress='lzw')
    with rasterio.open(mosaic_path, 'w', **out_meta) as dst:
        dst.write(mosaic)
    for ds in datasets:
        ds.close()
    print(f'  拼接完成: {mosaic.shape[2]}x{mosaic.shape[1]}')
else:
    with rasterio.open(mosaic_path) as src:
        print(f'  已有拼接: {src.width}x{src.height}')

# ============================================================
# Step 4: 按杭州行政边界精确裁剪 (非矩形框!)
# ============================================================
print(f'\n{"=" * 60}')
print('  Step 4: 按杭州边界精确裁剪')
print('=' * 60)

dem_final = os.path.join(RAW, 'hangzhou_dem_full.tif')
if os.path.exists(dem_final):
    with rasterio.open(dem_final) as src:
        geom_utm = hangzhou_4326.to_crs(src.crs).geometry.union_all()
        from shapely.geometry import box
        cov = geom_utm.intersection(box(*src.bounds)).area / geom_utm.area * 100
    if cov > 98:
        print(f'  已有裁剪结果, 覆盖 {cov:.1f}%')
        print(f'尺寸: {src.width}x{src.height}')
    else:
        os.remove(dem_final)
        print(f'  覆盖不足({cov:.1f}%), 重新裁剪')

if not os.path.exists(dem_final):
    with rasterio.open(mosaic_path) as src:
        # 多边形重投影到栅格CRS
        geom_utm = hangzhou_4326.to_crs(src.crs).geometry.union_all()
        print(f'  栅格CRS: {src.crs}, 边界CRS: {hangzhou_4326.crs}')
        print(f'  边界面积(WGS84): {geom_wgs84.area/1e6:.0f} km2')

        # 用精确多边形裁剪 — 不是 from_bounds!
        masked, out_transform = rasterio.mask.mask(
            src, [geom_utm], crop=True, nodata=-9999, all_touched=True
        )
        out_meta = src.meta.copy()
        out_meta.update(height=masked.shape[1], width=masked.shape[2],
                        transform=out_transform, nodata=-9999,
                        dtype='float32', compress='lzw')

    with rasterio.open(dem_final, 'w', **out_meta) as dst:
        dst.write(masked.astype('float32'))

    print(f'  裁剪完成: {masked.shape[2]}x{masked.shape[1]}')
    print(f'  有效像素: {(masked[0] > -9000).sum():,}')

# ============================================================
# 验证
# ============================================================
print(f'\n{"=" * 60}')
print('  数据获取完成')
with rasterio.open(dem_final) as src:
    data = src.read(1)
    valid = data > -9000
    print(f'  最终DEM: {src.width}x{src.height}')
    print(f'  CRS: {src.crs}')
    print(f'  分辨率: {src.res}')
    print(f'  有效像素: {valid.sum():,}')
    print(f'  高程范围: {data[valid].min():.0f} ~ {data[valid].max():.0f} m')
    print(f'  高程均值: {data[valid].mean():.1f} m')
print(f'{"=" * 60}')
