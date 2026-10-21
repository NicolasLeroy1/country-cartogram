"""Regenerate templates and historical examples from the bundled source fields."""
import csv, hashlib, json
from pathlib import Path
from country_cartogram.pipeline import ROOT as PACKAGE_ROOT, load_catalog, DEFAULTS
ROOT = Path(__file__).resolve().parents[1]
DATA = PACKAGE_ROOT / "data"
catalog=load_catalog(DATA/'countries.geojson')
fields=['country_code','value','name','cluster','parent','year']
for kind,field,year in [('gdp','GDP_MD','GDP_YEAR'),('population','POP_EST','POP_YEAR')]:
    included={code for code,r in catalog.items() if r['properties'][field]>0}
    with (ROOT/f'examples/{kind}.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for code,r in sorted(catalog.items()):
            if code not in included:continue
            parent=r['parent'] if r['parent'] in included else ''
            w.writerow({'country_code':code,'value':r['properties'][field], 'name':r['name'],
                        'cluster':catalog[parent]['cluster'] if parent else r['cluster'],
                        'parent':parent,'year':r['properties'][year]})
with (DATA/'country_catalog.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['country_code','name','cluster','parent']);w.writeheader()
    for r in sorted(catalog.values(),key=lambda r:r['country_code']):
        w.writerow({k:r[k] for k in w.fieldnames})
with (ROOT/'examples/your_values.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['country_code','value','name','cluster','parent']);w.writeheader()
    for r in sorted(catalog.values(),key=lambda r:r['country_code']):
        parent=r['parent']
        w.writerow({'country_code':r['country_code'],'value':'','name':r['name'],
                    'cluster':catalog[parent]['cluster'] if parent else r['cluster'],'parent':parent})
configs={
'gdp':{'title':'The world economy by size','subtitle':'Countries resized by nominal GDP | Historical demonstration dataset',
       'source_note':'Demo only: Natural Earth GDP_MD attributes, mixed years (mostly 2019); not current GDP. USD millions in input.',
       'prefix':'$','divisor':1000000,'suffix':'T','decimals':1,'label_countries':['AUS','BRA','IND','ZAF','NGA']},
'population':{'title':'The world by population','subtitle':'The same geography, resized by a different quantity',
       'source_note':'Demo only: Natural Earth POP_EST attributes, mixed historical years; not current population estimates.',
       'divisor':1000000,'suffix':'M','decimals':0,'label_countries':['AUS','BRA','ZAF']}}
for name,config in configs.items():
    (ROOT/f'examples/{name}.json').write_text(json.dumps(config,indent=2)+'\n')
(ROOT/'config.example.json').write_text(json.dumps(DEFAULTS,indent=2)+'\n')
(DATA/'sources.json').write_text(json.dumps({
 'provider':'Natural Earth','release_tag':'v5.1.2','dataset':'ne_50m_admin_0_countries.geojson',
 'url':'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_50m_admin_0_countries.geojson',
 'license':'Public domain','license_url':'https://www.naturalearthdata.com/about/terms-of-use/',
 'retrieved':'2026-10-21','sha256':hashlib.sha256((DATA/'countries.geojson').read_bytes()).hexdigest(),
 'quantity_note':'GDP_MD and POP_EST are historical source attributes with per-row years. Do not use as current statistics.'},indent=2)+'\n')
print('Prepared',len(catalog),'country and territory definitions')
