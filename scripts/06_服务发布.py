# -*- coding: utf-8 -*-
"""
脚本06：GeoServer 服务发布 —— 自动发布 WMS/WFS/WCS
"""
import os, sys, json, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT = os.path.join(BASE_DIR, '成果数据')

# GeoServer 配置
GEOSERVER_URL = os.getenv('GEOSERVER_URL', 'http://localhost:8080/geoserver')
GEO_USER = os.getenv('GEOSERVER_USER', 'admin')
GEO_PASS = os.getenv('GEOSERVER_PASSWORD', '')
WS_NAME = 'hangzhou_terrain'

# 数据库配置
DB_CONFIG = {
    'host': os.getenv('PGHOST', 'localhost'),
    'port': int(os.getenv('PGPORT', '5432')),
    'database': os.getenv('PGDATABASE', 'gis_practice'),
    'user': os.getenv('PGUSER', 'postgres'),
    'password': os.getenv('PGPASSWORD', ''),
    'schema': 'public',
}

# 栅格数据放这里（不含中文路径）
RASTER_DATA_DIR = os.path.join(BASE_DIR, 'raster_data')
os.makedirs(RASTER_DATA_DIR, exist_ok=True)

print('=' * 60)
print('  脚本06: GeoServer 服务发布')
print('=' * 60)


def gs(endpoint, method='get', body=None):
    url = f'{GEOSERVER_URL}/rest/{endpoint}'
    auth = (GEO_USER, GEO_PASS)
    headers = {'Content-Type': 'application/json'} if body else {}
    try:
        if method == 'get':
            return requests.get(url, auth=auth, timeout=10)
        elif method == 'post':
            return requests.post(url, auth=auth, data=body, headers=headers, timeout=10)
        elif method == 'put':
            return requests.put(url, auth=auth, data=body, headers=headers, timeout=10)
    except:
        return None


# ---- 1. 连接 ----
print('\n[1] 连接 GeoServer...')
r = gs('about/version.json')
if not r or r.status_code != 200:
    print(f'  [FAIL] 连不上，请确认 GeoServer 已启动')
    sys.exit(1)
print(f'  [OK] GeoServer {r.json()["about"]["resource"][0]["Version"]}')

# ---- 2. 创建 Workspace ----
print(f'\n[2] Workspace: {WS_NAME}...')
r = gs(f'workspaces/{WS_NAME}')
if r and r.status_code == 200:
    print(f'  [OK] 已存在')
else:
    r = gs('workspaces', 'post', json.dumps({'workspace': {'name': WS_NAME}}))
    if r and r.status_code == 201:
        print(f'  [OK] 创建成功')
    else:
        print(f'  [FAIL] {r.status_code if r else "NR"}'); sys.exit(1)

# ---- 3. PostGIS 存储 (矢量) ----
print(f'\n[3] PostGIS 存储...')
ds_body = json.dumps({
    'dataStore': {
        'name': 'postgis_terrain',
        'connectionParameters': {
            'entry': [
                {'@key': 'dbtype', '$': 'postgis'},
                {'@key': 'host', '$': DB_CONFIG['host']},
                {'@key': 'port', '$': str(DB_CONFIG['port'])},
                {'@key': 'database', '$': DB_CONFIG['database']},
                {'@key': 'user', '$': DB_CONFIG['user']},
                {'@key': 'passwd', '$': DB_CONFIG['password']},
                {'@key': 'schema', '$': DB_CONFIG['schema']},
            ]
        }
    }
})
r = gs(f'workspaces/{WS_NAME}/datastores', 'post', ds_body)
if r and r.status_code in (201, 409):
    print(f'  [OK] postgis_terrain')
else:
    print(f'  [WARN] {r.status_code if r else "NR"}')

# ---- 4. 发布矢量图层 ----
print(f'\n[4] 矢量图层...')
ft_body = json.dumps({
    'featureType': {'name': 'hangzhou_boundary', 'title': 'Hangzhou Boundary',
                    'srs': 'EPSG:4527'}
})
r = gs(f'workspaces/{WS_NAME}/datastores/postgis_terrain/featuretypes/hangzhou_boundary')
if r and r.status_code == 200:
    print(f'  [OK] hangzhou_boundary 已发布')
else:
    r = gs(f'workspaces/{WS_NAME}/datastores/postgis_terrain/featuretypes',
           'post', ft_body)
    if r and r.status_code == 201:
        print(f'  [OK] hangzhou_boundary')
    else:
        print(f'  [WARN] {r.status_code if r else "NR"}')

# ---- 5. 发布栅格图层 (GeoTIFF) ----
print(f'\n[5] 栅格图层 (GeoTIFF)...')
raster_list = [
    'dem_filled', 'slope', 'aspect', 'hillshade',
    'tri', 'tpi', 'elevation_zones', 'slope_zones', 'tpi_zones',
]

for name in raster_list:
    folder = '处理中' if name == 'dem_filled' else '成果数据'
    src = os.path.join(BASE_DIR, folder, f'{name}.tif')

    if not os.path.exists(src):
        print(f'  [SKIP] {name}: 文件缺失')
        continue

    # 复制到不含中文的目录
    dst = os.path.join(RASTER_DATA_DIR, f'{name}.tif')
    if not os.path.exists(dst):
        shutil.copy2(src, dst)

    # 创建 GeoTIFF coverage store
    cs_body = json.dumps({
        'coverageStore': {
            'name': name,
            'type': 'GeoTIFF',
            'enabled': True,
            'url': f'file://{dst.replace(chr(92), "/")}',
            'workspace': {'name': WS_NAME},
        }
    })

    r = gs(f'workspaces/{WS_NAME}/coveragestores/{name}')
    if r and r.status_code == 200:
        print(f'  [OK] {name} 已存在')
        continue

    r = gs(f'workspaces/{WS_NAME}/coveragestores', 'post', cs_body)
    if r and r.status_code == 201:
        # 自动发布 coverage
        cov_body = json.dumps({
            'coverage': {'name': name, 'title': name, 'srs': 'EPSG:4527'}
        })
        r2 = gs(f'workspaces/{WS_NAME}/coveragestores/{name}/coverages', 'post', cov_body)
        if r2 and r2.status_code == 201:
            print(f'  [OK] {name}')
        else:
            print(f'  [WARN] {name}: store OK, coverage fail ({r2.status_code if r2 else "NR"})')
    elif r and r.status_code == 409:
        print(f'  [INFO] {name} store已存在')
    else:
        print(f'  [WARN] {name}: {r.status_code if r else "NR"}')

# ---- 6. 端点 ----
print(f'\n[6] 服务端点:')
print(f'  WMS: {GEOSERVER_URL}/{WS_NAME}/wms?service=WMS&version=1.3.0&request=GetCapabilities')
print(f'  WCS: {GEOSERVER_URL}/{WS_NAME}/wcs?service=WCS&version=2.0.1&request=GetCapabilities')
print(f'  WFS: {GEOSERVER_URL}/{WS_NAME}/wfs?service=WFS&version=2.0.0&request=GetCapabilities')
print(f'\n  管理页面: {GEOSERVER_URL}/web/  (admin/geoserver)')

print(f'\n{"=" * 60}')
print(f'  发布完成')
print(f'{"=" * 60}')
