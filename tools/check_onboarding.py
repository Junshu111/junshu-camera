"""Read-only consistency checks for the public beginner onboarding materials."""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote
import json, re, sys

ROOT = Path(__file__).resolve().parents[1]
errors = []

class Prompt(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capture = False
        self.text = []
    def handle_starttag(self, tag, attrs):
        if dict(attrs).get('id') == 'ai-copy-text':
            self.capture = True
        elif self.capture and tag == 'br':
            self.text.append('\n')
    def handle_data(self, data):
        if self.capture:
            self.text.append(data)
    def handle_endtag(self, tag):
        if self.capture and tag == 'p':
            self.capture = False

setup = json.loads((ROOT/'content/setup.json').read_text())
roles = json.loads((ROOT/'workflow/roles.json').read_text())
ids = ['00', 'A', 'B', 'C', 'D', 'E', 'F']
if [r['id'] for r in roles] != ids:
    errors.append('Expected exactly 00 and A–F, in order.')
if len({r['title'] for r in roles}) != 7:
    errors.append('Role titles must be unique.')
for role in roles:
    file = ROOT/'workflow/handoffs'/f"{role['id']}.md"
    if not file.is_file():
        errors.append(f'Missing {file.relative_to(ROOT)}')
        continue
    text = file.read_text()
    for term in [role['title'], 'AI-WORKFLOW.md', '已读取交接，就绪', '交接未完成', '随后待命']:
        if term not in text:
            errors.append(f'{file.name}: missing {term}')
for page in ['docs/index.html', 'docs/setup.html']:
    parser = Prompt()
    source = (ROOT/page).read_text()
    parser.feed(source)
    if ''.join(parser.text) != setup['prompt']:
        errors.append(f'{page}: prompt differs from content/setup.json')
    if source.count('class="ai-web-step"') != 2:
        errors.append(f'{page}: expected exactly two copy steps')
    if '{{PROMPT}}' in source or '{{REPO}}' in source:
        errors.append(f'{page}: unresolved template')
if (ROOT/'docs/downloads/AI搭建环境提示词.txt').read_text().rstrip('\n') != setup['prompt']:
    errors.append('Downloaded prompt differs from the website source.')
for relative in ['START-HERE.md', 'AI-WORKFLOW.md', 'workflow/templates/STATUS.md', 'workflow/templates/THREADS.md']:
    if not (ROOT/relative).is_file():
        errors.append(f'Missing {relative}')
for file in [ROOT/name for name in ['README.md', 'START-HERE.md', 'AI-WORKFLOW.md', 'GETTING-STARTED.md']]:
    for ref in re.findall(r'\]\(([^)]+)\)', file.read_text()):
        url = urlsplit(ref)
        if not url.scheme and not url.netloc and not (file.parent/unquote(url.path)).exists():
            errors.append(f'{file.name}: missing link {ref}')
for item in ['README.md', 'AGENTS.md', 'GETTING-STARTED.md', 'AI-WORKFLOW.md', 'https://github.com/Junshu111/junshu-camera']:
    if item not in setup['prompt']:
        errors.append(f'Prompt must identify {item}')
if '.camera-work/' not in (ROOT/'.gitignore').read_text():
    errors.append('Personal workspace records should stay ignored.')
result = {'roles': len(roles), 'prompt_copies_checked': 3, 'markdown_links_checked': True, 'errors': errors}
print(json.dumps(result, ensure_ascii=False, indent=2))
sys.exit(bool(errors))
