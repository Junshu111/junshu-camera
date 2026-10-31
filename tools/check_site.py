"""Read-only, Python-standard-library checks for the tutorial website."""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote
import re, sys, json
ROOT=Path(__file__).resolve().parents[1]
SITE=ROOT/('site' if (ROOT/'site').is_dir() else 'docs')
class Links(HTMLParser):
    def __init__(self):super().__init__();self.refs=[];self.ids=set()
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if 'id' in a:self.ids.add(a['id'])
        for k in ('href','src'):
            if k in a:self.refs.append(a[k])
errors=[];parsers={};pages=sorted(SITE.rglob('*.html'))
for file in pages:
    parser=Links();parser.feed(file.read_text());parsers[file]=parser
for file,parser in parsers.items():
    for ref in parser.refs:
        u=urlsplit(ref)
        if u.scheme or u.netloc:continue
        dest=(file.parent/unquote(u.path)).resolve() if u.path else file.resolve()
        if not dest.exists():errors.append(f'{file.relative_to(SITE)}: missing {ref}')
        elif u.fragment and dest in parsers and unquote(u.fragment) not in parsers[dest].ids:errors.append(f'{file.name}: missing anchor {ref}')
for file in SITE.rglob('*.css'):
    for ref in re.findall(r'url\([\'\"]?([^\)\'\"]+)',file.read_text()):
        if not urlsplit(ref).scheme and not (file.parent/unquote(ref)).exists():errors.append(f'{file.name}: missing {ref}')
if len(pages)!=20:errors.append(f'Expected 20 pages, found {len(pages)}')
pdfs=list(SITE.rglob('*.pdf'))
approved=SITE/'downloads/diy-camera-guide-zh.pdf'
if pdfs!=[approved]:errors.append('Expected exactly the approved offline Chinese guide PDF.')
elif not approved.read_bytes().startswith(b'%PDF-'):errors.append('Invalid PDF header.')
result={'html_pages':len(pages),'chapters':len(list((SITE/'chapters').glob('*.html'))),'pdf_files':len(list(SITE.rglob('*.pdf'))),'errors':errors}
print(json.dumps(result,ensure_ascii=False,indent=2));sys.exit(bool(errors))
