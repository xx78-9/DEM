# -*- coding: utf-8 -*-
"""
脚本05：数据入库 —— 栅格 + 矢量导入 PostGIS
"""
import os, sys, tempfile, shutil, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import geopandas as gpd
import psycopg2

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT = os.path.join(BASE_DIR, '成果数据')

DB_CONFIG = {
    'host': os.getenv('PGHOST', 'localhost'),
    'port': int(os.getenv('PGPORT', '5432')),
    'database': os.getenv('PGDATABASE', 'gis_practice'),
    'user': os.getenv('PGUSER', 'postgres'),
    'password': os.getenv('PGPASSWORD', ''),
}

# 自动查找 raster2pgsql
def _find_raster2pgsql():
    """在 PATH 或常见目录中搜索 raster2pgsql"""
    for d in os.environ.get('PATH', '').split(os.pathsep):
        p = os.path.join(d, 'raster2pgsql.exe')
        if os.path.exists(p):
            return p
    for root, _, files in os.walk(r'C:\Program Files\PostgreSQL', followlinks=True):
        for f in files:
            if f == 'raster2pgsql.exe':
                return os.path.join(root, f)
    for root, _, files in os.walk(r'D:\Program Files\PostgreSQL', followlinks=True):
        for f in files:
            if f == 'raster2pgsql.exe':
                return os.path.join(root, f)
    return None

RASTER2PGSQL = _find_raster2pgsql()


def run():
    print('=' * 60)
    print('  脚本05：数据入库')
    print('=' * 60)
    print(f'  数据库: {DB_CONFIG["host"]}:{DB_CONFIG["port"]}/{DB_CONFIG["database"]}')

    # ---- 准备 ----
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute('CREATE EXTENSION IF NOT EXISTS postgis')
    cur.execute('CREATE EXTENSION IF NOT EXISTS postgis_raster')
    cur.close()
    conn.close()
    print('  [OK] PostGIS 扩展就绪')

    # ---- 栅格入库 ----
    print('\n  栅格入库...')
    raster_list = [
        'dem_filled', 'slope', 'aspect', 'hillshade',
        'tri', 'tpi', 'elevation_zones', 'slope_zones', 'tpi_zones',
    ]

    tmpdir = tempfile.mkdtemp(prefix='raster_')
    ok = 0

    for name in raster_list:
        folder = '处理中' if name == 'dem_filled' else '成果数据'
        src = os.path.join(BASE_DIR, folder, f'{name}.tif')
        if not os.path.exists(src):
            print(f'    [SKIP] {name}: 文件缺失')
            continue

        # 复制到临时目录 (raster2pgsql 不支持中文路径)
        tmp = os.path.join(tmpdir, f'{name}.tif')
        shutil.copy2(src, tmp)

        r = subprocess.run(
            [RASTER2PGSQL, '-s', '4527', '-I', '-C', '-M',
             '-t', '100x100', tmp, f'public.{name}'],
            capture_output=True, text=True, timeout=300
        )
        os.remove(tmp)

        if r.returncode != 0:
            print(f'    [FAIL] {name}: {r.stderr[:120]}')
            continue

        # 去掉末尾的 VACUUM (不能在事务中运行), 不影响数据
        sql = r.stdout.strip()
        if sql.endswith('VACUUM ANALYZE "public"."' + name + '";'):
            sql = sql[:-(len('VACUUM ANALYZE "public"."' + name + '";'))]

        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        cur = conn.cursor()
        try:
            cur.execute(f'DROP TABLE IF EXISTS public.{name} CASCADE')
            cur.execute(sql)
            print(f'    [OK] {name}')
            ok += 1
        except Exception as e:
            print(f'    [FAIL] {name}: {e}')
        finally:
            cur.close()
            conn.close()

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f'  栅格: {ok}/{len(raster_list)} 成功')

    # ---- 矢量入库 ----
    print('\n  矢量入库...')
    boundary = os.path.join(BASE_DIR, '原始数据', 'hangzhou_boundary_4527.geojson')
    if os.path.exists(boundary):
        gdf = gpd.read_file(boundary)
        from sqlalchemy import create_engine
        url = (f"postgresql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
               f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
        engine = create_engine(url)
        gdf.to_postgis('hangzhou_boundary', engine,
                       if_exists='replace', index=False)
        print(f'    [OK] hangzhou_boundary: {len(gdf)} 条')

    # ---- 验证 ----
    print('\n  验证:')
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename IN ('dem_filled','slope','aspect','hillshade',
                            'tri','tpi','elevation_zones','slope_zones',
                            'tpi_zones','hangzhou_boundary')
        ORDER BY tablename
    """)
    for (t,) in cur.fetchall():
        cur.execute(f'SELECT COUNT(*) FROM public.{t}')
        print(f'    public.{t}: {cur.fetchone()[0]} 行')
    cur.close()
    conn.close()

    print(f'\n{"=" * 60}')
    print(f'  入库完成')
    print(f'{"=" * 60}')


if __name__ == '__main__':
    run()
