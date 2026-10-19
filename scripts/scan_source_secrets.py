"""Offline source-pattern scan. Report paths/line numbers/rules, NEVER values.

Scans Git-tracked source plus explicitly included new files; --history scans unique
historical text blobs. It never reads .env, .local, user private keys or untracked
provider reference scripts. Pattern coverage is not proof that no secret exists.
"""
import argparse
import ast
import io
import json
from pathlib import Path
import re
import subprocess
import tokenize

ROOT = Path(__file__).resolve().parents[1]
GIT = ['git', '-c', 'safe.directory='+ROOT.as_posix()]
RULES = {
    'private-key-block': re.compile(r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'),
    'google-api-key': re.compile(r'AIza[0-9A-Za-z_-]{35}'),
    'github-token': re.compile(r'gh[pousr]_[A-Za-z0-9]{36,}'),
    'jwt': re.compile(r'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}'),
    'assigned-secret': re.compile(r'''(?i)(?:api_key|client_secret|access_token|licen[cs]e_key)\s*[:=]\s*["']([A-Za-z0-9_+/=-]{32,})["']'''),
}
TEXT = {'.py','.ts','.tsx','.js','.mjs','.json','.yaml','.yml','.toml','.ini','.bicep','.sh','.md','.txt','.example','.css','.html','.svg',''}
REFERENCES = {'gemini_contract_test.py','gemini_simple_test.py'}


def allowed(name):
    p=Path(name)
    return (p.suffix.lower() in TEXT and p.name not in REFERENCES
            and not set(p.parts)&{'.local','.git','.venv','node_modules','.next','.ssh'}
            and (not p.name.startswith('.env') or p.name.endswith('.example')))


def marker_only_literal(line, match):
    try:
        for token in tokenize.generate_tokens(io.StringIO(line).readline):
            if token.type==tokenize.STRING and token.start[1]<=match.start()<token.end[1]:
                return ast.literal_eval(token.string)==match.group(0)
    except (SyntaxError, ValueError, tokenize.TokenError):pass
    return False


def findings(name, content, reviewed=None):
    result=[]
    for number,line in enumerate(content.splitlines(),1):
        for rule,pattern in RULES.items():
            for match in pattern.finditer(line):
                if rule=='private-key-block' and marker_only_literal(line,match):
                    if reviewed is not None:reviewed.append({'path':name,'line':number,'rule':rule,'reason':'Exact marker-only literal, with no key body'})
                    continue
                if rule=='assigned-secret' and match[1].lower().startswith(('synthetic','mock','test_','example','your_','replace')):continue
                result.append({'path':name,'line':number,'rule':rule})
    return result


def scan(include_new=False,history=False):
    paths=subprocess.check_output([*GIT,'ls-files'],cwd=ROOT,text=True).splitlines()
    if include_new:paths+=subprocess.check_output([*GIT,'ls-files','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
    matches=[];reviewed=[];checked=0;history_checked=0;skipped=[]
    for name in sorted(set(paths)):
        if not allowed(name):continue
        p=ROOT/name
        if p.stat().st_size>16*1024*1024:skipped.append(name);continue
        content=p.read_text(encoding='utf-8',errors='replace');checked+=1
        matches+=findings(name,content,reviewed)
    if history:
        objects=subprocess.check_output([*GIT,'rev-list','--objects','--all'],cwd=ROOT,text=True).splitlines()
        for row in objects:
            oid,_,name=row.partition(' ')
            if not name or not allowed(name):continue
            # rev-list includes tree paths too; only blobs are source content.
            kind=subprocess.check_output([*GIT,'cat-file','-t',oid],cwd=ROOT,text=True).strip()
            if kind!='blob':continue
            size=int(subprocess.check_output([*GIT,'cat-file','-s',oid],cwd=ROOT,text=True))
            if size>16*1024*1024:skipped.append('history:'+name);continue
            content=subprocess.check_output([*GIT,'cat-file','blob',oid],cwd=ROOT).decode('utf-8',errors='replace')
            history_checked+=1
            matches+=findings('history:'+name,content,reviewed)
    return {'source_files':checked,'historical_text_blobs':history_checked,'matches':matches,
            'reviewed_marker_literals':reviewed,
            'oversize_skipped':skipped,'values_disclosed':False,'limitations':'Pattern scan; private environments, ignored data and supplied untracked live scripts intentionally excluded.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--include-new',action='store_true');p.add_argument('--history',action='store_true')
    p.add_argument('--output')
    a=p.parse_args();result=scan(a.include_new,a.history)
    if a.output:
        target=Path(a.output).resolve()
        if not target.is_relative_to(ROOT/'.local'):raise ValueError('Use ignored .local for scan artifacts')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2));raise SystemExit(bool(result['matches']))
