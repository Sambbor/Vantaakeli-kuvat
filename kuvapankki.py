#!/usr/bin/env python3
"""Kuvapankki – Vantaan hoitourakan kelikameroiden kuvat ja tiesääasemien mittaukset opetusaineistoksi.

Ajetaan GitHub Actionsissa kerran tunnissa (säilö Sambbor/Vantaakeli-kuvat).
- Kamerat, joiden kuvasuunta on enintään PAIR_MAX m päässä tiesääasemasta ("pari"): kuva joka tunti,
  ja viereen aseman mittaukset samalta hetkeltä (keli, varoitus, vesi/lumi/jää, kitka, suola, tienpinta, kuurapiste ...).
  Aseman mittaus on kuvan "oikea vastaus", kun kameran tunnistusta opetetaan.
- Muut urakan kamerat ("vertailu"): kuva 3 tunnin välein, kun talvikeli on mahdollinen (kylmin asema ≤ +3 °C tai
  jollain asemalla muu kuin kuiva keli). Niillä testataan myöhemmin, toimiiko tunnistus ilman asemaa.
- Kuivan kelin kuvista tallennetaan vain osa, talviset tilanteet ja kelin vaihtumiset aina.
Data: Fintraffic / Digitraffic, CC BY 4.0.
"""
import gzip, io, json, math, os, time, urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from PIL import Image

VERSION = 'kp2'
DT = 'https://tie.digitraffic.fi'
HDR = {'Digitraffic-User': 'Vantaa-urakka-kuvapankki', 'User-Agent': 'vantaa-kuvapankki/1', 'Accept-Encoding': 'gzip'}
BBOX = (24.55, 60.15, 25.45, 60.50)
CONTRACT = 301                    # Vantaan hoitourakka (Digitrafficin contractAreaCode)
PAIR_MAX = 300                    # m: kamera ja asema samassa paikassa ("pari")
NEAR_MAX = 1000                   # m: asema lähellä ("lähi"), merkintä hieman epävarmempi; tallennetaan kuten parit
IMG_W = 400                       # tallennettavan kuvan leveys (px)
JPEG_Q = 78
STALE_MIN = 40                    # vanhempia kamerakuvia ei tallenneta
LABEL_MAX_MIN = 25                # aseman mittaus enintään näin kaukana kuvan ajasta, muuten merkintä "label_ok=0"
COMPARE_EVERY_H = 3               # vertailukameroiden väli
SIZE_SOFT_MB = 900                # kun kuvia on tätä enemmän, kuivia kuvia harvennetaan lisää
TZ = ZoneInfo('Europe/Helsinki')
M_LAT = 111320.0
M_LON = 111320.0 * math.cos(math.radians(60.3))
LAT0, LON0 = 60.30, 24.97
# KELI: 0 vika, 1 kuiva, 2 kostea, 3 märkä, 4 märkä ja suolattu, 5 kuura, 6 lumi, 7 jää, 8 tod. kostea ja suolainen, 9 sohjoinen
KELI_FI = {0: 'vika', 1: 'kuiva', 2: 'kostea', 3: 'märkä', 4: 'märkä+suola', 5: 'kuura', 6: 'lumi', 7: 'jää', 8: 'kostea+suola', 9: 'sohjo'}
WINTER_KELI = {5, 6, 7, 9}
WET_KELI = {2, 3, 4, 8}
# tallennusosuus kelin mukaan (1 = kaikki): kuivasta joka 4., märästä joka 2., talvisesta kaikki
KEEP_EVERY = {'kuiva': 4, 'märkä': 2, 'talvi': 1}
COLS = ['t', 'file', 'kind', 'cam', 'preset', 'dir', 'road', 'img_t', 'station', 'dist_m', 'st_t', 'label_ok',
        'keli', 'keli_fi', 'keli2', 'warn', 'tr', 'ta', 'td', 'fp', 'tf', 'water', 'snow', 'ice', 'fric', 'salt',
        'rain', 'rain_i', 'rain_form', 'vis', 'ws', 'sun', 'mean_L', 'reason', 'v']

OUT = 'data'
IMG = 'img'


def get(url, raw=False, timeout=40):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
        if r.headers.get('Content-Encoding', '').lower() == 'gzip' or b[:2] == b'\x1f\x8b':
            b = gzip.decompress(b)
    return b if raw else json.loads(b)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_t(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp() if s else None


def load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj, indent=None):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)


def dist(a, b):
    return math.hypot((a[0] - b[0]) * M_LON, (a[1] - b[1]) * M_LAT)


def sun_elev(t, lat=LAT0, lon=LON0):
    n = t / 86400.0 + 2440587.5 - 2451545.0
    L = (280.460 + 0.9856474 * n) % 360
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)))
    gmst = (18.697374558 + 24.06570982441908 * n) % 24
    ha = math.radians(gmst * 15 + lon - ra)
    la = math.radians(lat)
    return round(math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))), 1)


def frost_pt(td):
    """Kuurapiste kastepisteestä (sama kaava kuin kelisivun säännöissä)."""
    if td is None:
        return None
    if td >= 0:
        return td
    g = 17.62 * td / (243.12 + td)
    return round(272.62 * g / (22.46 - g), 2)


def cameras():
    """Urakan kamerat ja kuvasuunnat sekä lähin tiesääasema (välimuisti 7 vrk)."""
    c = load(OUT + '/cams.json', None)
    if c and time.time() - c.get('t', 0) < 7 * 86400 and c.get('v') == VERSION:
        return c['cams']
    ws = {}
    for f in get(DT + '/api/weather/v1/stations')['features']:
        ws[f['id']] = f['geometry']['coordinates'][:2]
    cams = []
    for f in get(DT + '/api/weathercam/v1/stations')['features']:
        x, y = f['geometry']['coordinates'][:2]
        if not (BBOX[0] <= x <= BBOX[2] and BBOX[1] <= y <= BBOX[3]) or f['properties'].get('collectionStatus') != 'GATHERING':
            continue
        try:
            d = get(DT + '/api/weathercam/v1/stations/' + f['id'])
        except Exception:
            continue
        p0 = d['properties']
        ra = p0.get('roadAddress') or {}
        if ra.get('contractAreaCode') != CONTRACT:
            continue
        # lähin asema: Digitrafficin oma tieto, tarkistetaan etäisyys; jos sitä ei ole, haetaan lähin itse
        sid = p0.get('nearestWeatherStationId')
        if sid not in ws:
            sid = min(ws, key=lambda i: dist(ws[i], (x, y))) if ws else None
        dm = round(dist(ws[sid], (x, y))) if sid in ws else None
        name = (p0.get('names') or {}).get('fi') or p0.get('name')
        for p in p0.get('presets', []):
            if not p.get('inCollection', True):
                continue
            cams.append({'cam': d['id'], 'preset': p['id'], 'name': name, 'dir': p.get('presentationName') or '',
                         'road': ra.get('roadNumber'), 'lon': x, 'lat': y, 'station': sid, 'dist_m': dm,
                         'kind': 'pari' if dm is not None and dm <= PAIR_MAX else 'lähi' if dm is not None and dm <= NEAR_MAX else 'vertailu',
                         'url': p.get('imageUrl') or ('https://weathercam.digitraffic.fi/' + p['id'] + '.jpg')})
    save(OUT + '/cams.json', {'t': time.time(), 'v': VERSION, 'cams': cams}, indent=1)
    return cams


def lane_val(v, base, lanes=(1, 2)):
    for n in lanes:
        for k in (base + str(n), base + '_' + str(n)):
            if k in v:
                return v[k]
    return None


def station_label(sv):
    """Aseman mittaukset kuvan merkinnäksi. sv = {anturin nimi: {value, measuredTime}}"""
    val = lambda k: (sv.get(k) or {}).get('value')
    ts = [parse_t(x.get('measuredTime')) for x in sv.values() if x.get('measuredTime')]
    keli = val('KELI_1')
    if keli in (None, 0):
        keli = next((val('KELI_%d' % n) for n in (2, 3, 4) if val('KELI_%d' % n) not in (None, 0)), keli)
    warns = [val('VAROITUS_%d' % n) for n in (1, 2, 3, 4) if val('VAROITUS_%d' % n) is not None]
    rank = {0: 0, 4: 1, 1: 2, 3: 3, 2: 4}
    td = val('KASTEPISTE')
    fr = [x for x in (val('KITKA1_LUKU'), val('KITKA2_LUKU')) if x is not None]
    return {
        'st_t': iso(max(ts)) if ts else None,
        'keli': keli, 'keli_fi': KELI_FI.get(keli, ''), 'keli2': val('KELI_2'),
        'warn': max(warns, key=lambda w: rank.get(w, 0)) if warns else None,
        'tr': val('TIE_1'), 'ta': val('ILMA'), 'td': td, 'fp': val('KUURAPISTE') if val('KUURAPISTE') is not None else frost_pt(td),
        'tf': val('JÄÄTYMISPISTE_1'), 'water': val('VEDEN_MÄÄRÄ1'), 'snow': val('LUMEN_MÄÄRÄ1'), 'ice': val('JÄÄN_MÄÄRÄ1'),
        'fric': min(fr) if fr else None, 'salt': val('SUOLAN_MÄÄRÄ_1') if val('SUOLAN_MÄÄRÄ_1') is not None else val('SUOLAN_MÄÄRÄ1'),
        'rain': val('SADE'), 'rain_i': val('SADE_INTENSITEETTI'), 'rain_form': val('SATEEN_OLOMUOTO_PWDXX'),
        'vis': val('NÄKYVYYS_KM'), 'ws': val('KESKITUULI'),
    }


def keli_group(lab):
    k = lab.get('keli')
    if k in WINTER_KELI or lab.get('warn') in (2, 3) or (lab.get('ice') or 0) >= 0.1 or (lab.get('snow') or 0) > 0.2 \
            or (lab.get('fric') is not None and lab['fric'] < 0.45):
        return 'talvi'
    if k in WET_KELI or (lab.get('water') or 0) > 0.03 or (lab.get('rain') or 0) > 0:
        return 'märkä'
    return 'kuiva'


def csv_cell(v):
    if v is None:
        return ''
    s = str(v)
    return '"' + s.replace('"', '""') + '"' if any(c in s for c in ',"\n') else s


def append_csv(path, rows):
    new = not os.path.exists(path)
    with open(path, 'a', encoding='utf-8') as f:
        if new:
            f.write(','.join(COLS) + '\n')
        for r in rows:
            f.write(','.join(csv_cell(r.get(c)) for c in COLS) + '\n')


def main():
    now = time.time()
    loc = datetime.fromtimestamp(now, TZ)
    os.makedirs(OUT, exist_ok=True)
    cams = cameras()
    state = load(OUT + '/state.json', {})
    stats = load(OUT + '/index.json', {'n': 0, 'bytes': 0, 'by_keli': {}, 'by_kind': {}, 'by_preset': {}})
    # kuvien ajat ja asemien mittaukset kerralla
    cam_t = {}
    for s in get(DT + '/api/weathercam/v1/stations/data').get('stations', []):
        for p in s.get('presets', []):
            cam_t[p['id']] = p.get('measuredTime')
    st_vals = {}
    for s in get(DT + '/api/weather/v1/stations/data').get('stations', []):
        st_vals[s['id']] = {v['name']: v for v in s.get('sensorValues', [])}
    # talvikeli mahdollinen: urakan asemien kylmin tienpinta ≤ +3 °C tai jollain asemalla muu kuin kuiva keli
    own = {c['station'] for c in cams if c.get('station') in st_vals}
    labs = {i: station_label(st_vals[i]) for i in own}
    trs = [l['tr'] for l in labs.values() if l['tr'] is not None]
    wintry = (min(trs) <= 3 if trs else False) or any(keli_group(l) != 'kuiva' for l in labs.values())
    compare_now = wintry and loc.hour % COMPARE_EVERY_H == 0
    big = stats.get('bytes', 0) > SIZE_SOFT_MB * 1e6
    sun = sun_elev(now)
    month = loc.strftime('%Y-%m')
    rows, skipped = [], {'vanha': 0, 'sama': 0, 'harvennus': 0, 'vertailu': 0, 'virhe': 0}
    for c in cams:
        pid = c['preset']
        st = state.setdefault(pid, {})
        if c['kind'] == 'vertailu' and not compare_now:
            skipped['vertailu'] += 1
            continue
        mt = parse_t(cam_t.get(pid))
        if mt is None or now - mt > STALE_MIN * 60:
            skipped['vanha'] += 1
            continue
        if st.get('img_t') == cam_t.get(pid):
            skipped['sama'] += 1
            continue
        lab = labs.get(c['station']) or (station_label(st_vals[c['station']]) if c['station'] in st_vals else {})
        grp = keli_group(lab) if lab else 'kuiva'
        changed = lab and st.get('last_keli') is not None and lab.get('keli') != st.get('last_keli')
        cnt = st.get('cnt_' + grp, 0) + 1
        every = 1 if c['kind'] == 'vertailu' else KEEP_EVERY[grp] * (4 if big and grp == 'kuiva' else 1)   # vertailukamerat jo harvaan
        reason = 'muutos' if changed else grp
        st['cnt_' + grp] = cnt
        st['last_keli'] = lab.get('keli') if lab else None
        if not changed and (cnt - 1) % every != 0:   # ensimmäinen kuva aina
            skipped['harvennus'] += 1
            continue
        try:
            img = Image.open(io.BytesIO(get(c['url'], raw=True)))
            img.load()
            im = img.convert('RGB')
            w, h = im.size
            if w > IMG_W:
                im = im.resize((IMG_W, round(h * IMG_W / w)), Image.LANCZOS)
            small = im.resize((40, 22), Image.BOX)
            px = list(small.getdata())
            mean_L = round(sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px) / len(px), 1)
            rel = '%s/%s/%s_%s.jpg' % (IMG, month, pid, datetime.fromtimestamp(mt, TZ).strftime('%Y%m%d-%H%M'))
            os.makedirs(os.path.dirname(rel), exist_ok=True)
            im.save(rel, 'JPEG', quality=JPEG_Q, optimize=True)
            size = os.path.getsize(rel)
        except Exception as e:
            print('kuva %s: %s' % (pid, e))
            skipped['virhe'] += 1
            continue
        st['img_t'] = cam_t.get(pid)
        st_t = parse_t(lab.get('st_t')) if lab else None
        row = {'t': iso(now), 'file': rel, 'kind': c['kind'], 'cam': c['cam'], 'preset': pid, 'dir': c['dir'], 'road': c['road'],
               'img_t': iso(mt), 'station': c['station'], 'dist_m': c['dist_m'],
               'label_ok': 1 if st_t and abs(st_t - mt) <= LABEL_MAX_MIN * 60 else 0,
               'sun': sun, 'mean_L': mean_L, 'reason': reason, 'v': VERSION}
        row.update(lab or {})
        rows.append(row)
        stats['n'] = stats.get('n', 0) + 1
        stats['bytes'] = stats.get('bytes', 0) + size
        k = row.get('keli_fi') or '?'
        stats['by_keli'][k] = stats['by_keli'].get(k, 0) + 1
        stats['by_kind'][c['kind']] = stats['by_kind'].get(c['kind'], 0) + 1
        stats['by_preset'][pid] = stats['by_preset'].get(pid, 0) + 1
    if rows:
        append_csv('%s/labels-%s.csv' % (OUT, month), rows)
    stats.update({'updated': iso(now), 'v': VERSION, 'last_saved': len(rows), 'last_skipped': skipped, 'wintry': wintry,
                  'cams': len({c['cam'] for c in cams}), 'presets': len(cams),
                  'pairs': sorted({'%s→%s (%s m, %s)' % (c['cam'], c['station'], c['dist_m'], c['kind']) for c in cams if c['kind'] != 'vertailu'})})
    if not stats.get('first'):
        stats['first'] = iso(now)
    save(OUT + '/state.json', state)
    save(OUT + '/index.json', stats, indent=1)
    kinds = {}
    for c in cams:
        kinds[c['kind']] = kinds.get(c['kind'], 0) + 1
    print('Tallennettu %d kuvaa (%s), ohitettu %s; yhteensä %d kuvaa, %.1f Mt; kuvasuunnat %s' % (
        len(rows), ', '.join('%s %d' % (r['preset'], r.get('keli') or 0) for r in rows[:6]) + (' …' if len(rows) > 6 else ''),
        skipped, stats['n'], stats['bytes'] / 1e6, kinds))


if __name__ == '__main__':
    main()
