"""Portable, read-only reference checks. Never imports camera code or touches devices."""
from pathlib import Path
import ast, hashlib, json, math, struct, sys, tarfile, zipfile
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
errors=[]
counts={'source_files':0,'python_files':0,'software_archives':0,'stl_files':0,'project_3mf':0,'freecad_files':0}

def sha(data):return hashlib.sha256(data).hexdigest()
def safe(name):return not name.startswith(('/', '\\')) and '..' not in Path(name).parts
def check_archive(name,source):
    path=ROOT/'software/dist-1.0'/name
    expected=path.with_name(path.name+'.sha256').read_text().split()[0]
    assert sha(path.read_bytes())==expected,'archive sidecar mismatch'
    with tarfile.open(path,'r:gz') as tf:
        members=tf.getmembers()
        assert all(m.isfile() and safe(m.name) for m in members),'unexpected archive entry'
        data={m.name.split('/',1)[1]:tf.extractfile(m).read() for m in members}
        manifest={line.split('  ',1)[1]:line.split('  ',1)[0] for line in data['MANIFEST.sha256'].decode().splitlines()}
        assert set(data)==set(manifest)|{'MANIFEST.sha256'},'release entry mismatch'
        for file,digest in manifest.items():
            assert sha(data[file])==digest,'release manifest mismatch: '+file
            assert (ROOT/'software'/source/file).read_bytes()==data[file],'frozen source mismatch: '+file
    counts['software_archives']+=1

for item in json.loads((ROOT/'SOURCE-MANIFEST.json').read_text()):
    try:
        assert safe(item['path'])
        p=ROOT/item['path']
        assert p.is_file() and not p.is_symlink()
        assert p.stat().st_size==item['bytes'] and sha(p.read_bytes())==item['sha256']
        counts['source_files']+=1
    except Exception as exc:errors.append(item['path']+': '+str(exc))
for p in (ROOT/'software').rglob('*.py'):
    try:ast.parse(p.read_bytes(),filename=p.name);counts['python_files']+=1
    except Exception as exc:errors.append(str(p.relative_to(ROOT))+': '+str(exc))
for name,source in [('junshu-camera-pi-1.0.0.tar.gz','pi-camera-1.0'),('junshu-camera-boot-1.0.0.tar.gz','pi-boot-logo-1.0')]:
    try:check_archive(name,source)
    except Exception as exc:errors.append(name+': '+str(exc))
for p in (ROOT/'models').rglob('*.stl'):
    try:
        data=p.read_bytes();n=struct.unpack_from('<I',data,80)[0]
        assert n>0 and len(data)==84+50*n,'invalid binary STL length'
        for off in range(84,len(data),50):
            assert all(math.isfinite(x) for x in struct.unpack_from('<12f',data,off)),'non-finite mesh coordinate'
        counts['stl_files']+=1
    except Exception as exc:errors.append(str(p.relative_to(ROOT))+': '+str(exc))
for p in list((ROOT/'models').rglob('*.3mf'))+list((ROOT/'models').rglob('*.FCStd')):
    try:
        with zipfile.ZipFile(p) as z:
            assert all(safe(n) for n in z.namelist()),'unsafe ZIP entry'
            assert z.testzip() is None,'ZIP CRC mismatch'
            if p.suffix=='.3mf':
                models=[n for n in z.namelist() if n.endswith('.model')]
                assert models,'missing model resource'
                for name in models:
                    xml=ET.fromstring(z.read(name))
                    for vertex in xml.iter():
                        if vertex.tag.endswith('}vertex'):
                            assert all(math.isfinite(float(vertex.attrib[c])) for c in ['x','y','z'])
                counts['project_3mf']+=1
            else:
                assert 'Document.xml' in z.namelist() and 'GuiDocument.xml' in z.namelist()
                counts['freecad_files']+=1
    except Exception as exc:errors.append(str(p.relative_to(ROOT))+': '+str(exc))
print(json.dumps({'checks':counts,'errors':errors,'scope':'offline reference integrity; not hardware or printer acceptance'},ensure_ascii=False,indent=2))
sys.exit(bool(errors))
