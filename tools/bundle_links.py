#!/usr/bin/env python3
"""Resolve shipped Markdown links, retaining local links when targets ship too."""
import argparse
from pathlib import Path
import re
from urllib.parse import quote, unquote

LINK=re.compile(r'(\]\()([^\s)]+)(\))')

def rewrite(repo, component, stage, ref):
    sources={}
    for doc in stage.rglob('*.md'):
        rel=doc.relative_to(stage)
        original=repo/component/rel
        if component=='wan-watchdog' and str(rel)=='README.md':original=repo/component/'BUNDLE-README.md'
        if not original.exists():original=repo/doc.name
        if not original.is_file():raise ValueError(f'No source for {rel}')
        sources[original.resolve()]=doc
    # Non-Markdown files and directories can be link targets, too.
    for dest in stage.rglob('*'):
        rel=dest.relative_to(stage);original=repo/component/rel
        if original.exists():sources.setdefault(original.resolve(),dest)
    sources[(repo/'LICENSE').resolve()]=stage/'LICENSE'
    def update(doc,original):
        def link(m):
            value=m[2]
            if ':' in value or value.startswith(('#','/')):return m[0]
            path,mark,anchor=value.partition('#')
            source=(original.parent/unquote(path)).resolve()
            if not source.exists() or not source.is_relative_to(repo.resolve()):
                raise ValueError(f'Broken source link: {original}: {value}')
            dest=sources.get(source)
            if dest is not None and dest.exists():
                import os
                target=Path(os.path.relpath(dest,doc.parent)).as_posix()
            else:
                kind='tree' if source.is_dir() else 'blob'
                target=f'https://github.com/aiordanescu/asuswrt-merlin-tools/{kind}/{quote(ref,safe="")}/{quote(source.relative_to(repo.resolve()).as_posix())}'
            return m[1]+target+(mark+anchor if mark else '')+m[3]
        doc.write_text(LINK.sub(link,doc.read_text()))
    for original,doc in list(sources.items()):
        if doc.suffix=='.md':update(doc,original)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('component',choices=['wan-affinity','wan-watchdog']);p.add_argument('stage',type=Path);p.add_argument('--ref',default='main')
    a=p.parse_args();rewrite(Path(__file__).resolve().parents[1],a.component,a.stage,a.ref)
