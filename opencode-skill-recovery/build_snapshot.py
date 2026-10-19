"""Build a byte-exact, content-addressed skill migration archive."""
import hashlib
import json
import pathlib
import zipfile

BASE = pathlib.Path(__file__).resolve().parent
HOME = pathlib.Path.home()
WORK = pathlib.Path('D:/workspace')
EXCLUDE = {'.git', 'node_modules', '__pycache__', '.pytest_cache'}


def digest(files):
    h = hashlib.sha256()
    for name, data in sorted(files.items()):
        h.update(name.encode('utf-8') + b'\0')
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def main():
    discovered = (BASE / 'sources.txt').read_text(encoding='utf-8-sig').splitlines()
    entries, objects = [], {}
    for source in sorted(set(discovered)):
        p = pathlib.Path(source)
        if p.name != 'SKILL.md' or BASE in p.parents:
            continue
        standalone = p.parent == HOME / 'Downloads'
        files = {'SKILL.md': p.read_bytes()} if standalone else {
            f.relative_to(p.parent).as_posix(): f.read_bytes()
            for f in sorted(p.parent.rglob('*')) if f.is_file()
            and not EXCLUDE.intersection(f.relative_to(p.parent).parts)
        }
        key = digest(files)
        objects.setdefault(key, files)
        root = p.parent
        managed = any(x in root.parts for x in ('plugins', 'extensions', 'builtin', '.cache'))
        scope = 'reference' if standalone else ('managed' if managed else 'active')
        anchor = 'home' if root.is_relative_to(HOME) else 'workspace'
        relative = root.relative_to(HOME if anchor == 'home' else WORK).as_posix()
        entries.append(dict(source=str(root), anchor=anchor, relative=relative,
                            name=root.name if not standalone else 'transfer-client-cpp-style',
                            scope=scope, object=key))
    manifest = dict(format=1, date='2026-10-19', deduplication='SHA256 of sorted relative paths and raw file SHA256 digests',
                    exclusions=sorted(EXCLUDE), entries=entries,
                    objects={k: {n: hashlib.sha256(b).hexdigest() for n,b in sorted(v.items())} for k,v in sorted(objects.items())})
    (BASE / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    with zipfile.ZipFile(BASE / 'skills-snapshot.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for key, files in sorted(objects.items()):
            for name, data in sorted(files.items()):
                z.writestr(f'objects/{key}/{name}', data)
    archive = BASE / 'skills-snapshot.zip'
    (BASE / 'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  skills-snapshot.zip\n', encoding='ascii')
    print(json.dumps(dict(sources=len(entries), unique=len(objects), files=sum(map(len, objects.values())), zip_bytes=archive.stat().st_size, scopes={s:sum(e['scope']==s for e in entries) for s in ('active','managed','reference')})))


if __name__ == '__main__':
    main()
