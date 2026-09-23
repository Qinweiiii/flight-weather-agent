"""Fetch pinned browser dependencies; no runtime CDN is required afterwards."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    'react.js': 'react@18.3.1/umd/react.production.min.js',
    'react-dom.js': 'react-dom@18.3.1/umd/react-dom.production.min.js',
    'babel.js': '@babel/standalone@7.26.4/babel.min.js',
    'marked.js': 'marked@12.0.2/marked.min.js',
    'purify.js': 'dompurify@3.3.1/dist/purify.min.js',
    'echarts.js': 'echarts@5.5.0/dist/echarts.min.js',
    'react.LICENSE': 'react@18.3.1/LICENSE',
    'react-dom.LICENSE': 'react-dom@18.3.1/LICENSE',
    'babel.LICENSE': '@babel/standalone@7.26.4/LICENSE',
    'marked.LICENSE': 'marked@12.0.2/LICENSE.md',
    'purify.LICENSE': 'dompurify@3.3.1/LICENSE',
    'echarts.LICENSE': 'echarts@5.5.0/LICENSE',
}


def main():
    folder = ROOT/'static/vendor'
    folder.mkdir(parents=True,exist_ok=True)
    manifest={}
    for name, package in ASSETS.items():
        url='https://cdn.jsdelivr.net/npm/'+package
        with urllib.request.urlopen(url,timeout=45) as response:
            data=response.read()
        (folder/name).write_bytes(data)
        manifest[name]={'url':url,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
        print(name,len(data))
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
