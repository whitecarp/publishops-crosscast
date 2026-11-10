"""Execute the production notice predicate in Node; no browser or account data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from video_publisher.work_delete_douyin import GONE_SNAPSHOT


URL = 'https://www.douyin.com/video/7700000000000000000'
RUNNER = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
class Element {
  constructor(spec, parent=null) {
    this.parent = parent; this.tag = spec.tag || 'div'; this.attrs = spec.attrs || {};
    this.innerText = spec.text || ''; this.isConnected = spec.connected !== false;
    this.visible = spec.visible !== false; this.visibility = spec.visibility || 'visible';
    this.width = spec.width ?? 1; this.height = spec.height ?? 1;
    this.children = (spec.children || []).map(child => new Element(child, this));
  }
  matches(selector) {
    if (selector === 'article') return this.tag === 'article';
    if (selector === '.semi-modal-content') return (this.attrs.class || '').split(/\s+/).includes('semi-modal-content');
    const match = selector.match(/^\[([\w-]+)(?:(\^?=)"([^"]*)")?\]$/);
    if (!match) throw new Error('Unsupported fixture selector: ' + selector);
    const value = this.attrs[match[1]];
    return value !== undefined && (!match[2] || (match[2] === '^=' ? value.startsWith(match[3]) : value === match[3]));
  }
  closest(selector) {
    for (let item=this; item; item=item.parent)
      if (selector.split(',').some(part => item.matches(part.trim()))) return item;
    return null;
  }
  getClientRects() { return this.visible && !this.closest('[hidden]') ? [{width: this.width, height: this.height}] : []; }
}
function flatten(node) { return [node, ...node.children.flatMap(flatten)]; }
const location = {href: input.url};
const context = vm.createContext({location, getComputedStyle: node => ({visibility: node.visibility})});
const evaluate = vm.runInContext('(' + input.source + ')', context, {timeout: 1000});
const output = input.fixtures.map(spec => {
  const root = new Element(spec.root);
  const nodes = flatten(root).filter(node => node.attrs.role === 'alert');
  location.href = spec.url || input.url;
  return evaluate(nodes, input.url);
});
process.stdout.write(JSON.stringify(output));
"""


def alert(text='该作品已被删除', **extra):
    return {'attrs': {'role': 'alert'}, 'text': text, **extra}


class DouyinNoticeScriptTests(unittest.TestCase):
    def test_atomic_snapshot_requires_exact_page_and_one_current_platform_notice(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is required to execute the production DOM predicate')
        cases = [
            ('platform notice', {'children': [alert()]}, 'deleted_notice'),
            ('same words inside a replaced user card',
             {'attrs': {'class': 'video-card-content-123'}, 'children': [alert()]}, 'no_platform_alert'),
            ('notice reparented into an article', {'tag': 'article', 'children': [alert()]}, 'no_platform_alert'),
            ('notice reparented into a dialog', {'attrs': {'role': 'dialog'}, 'children': [alert()]}, 'no_platform_alert'),
            ('notice reparented into a modal', {'attrs': {'class': 'semi-modal-content'}, 'children': [alert()]}, 'no_platform_alert'),
            ('second eligible alert', {'children': [alert(), alert()]}, 'ambiguous_alerts'),
            ('detached original notice', {'children': [alert(connected=False)]}, 'no_platform_alert'),
            ('hidden ancestor', {'attrs': {'hidden': ''}, 'children': [alert()]}, 'no_platform_alert'),
            ('aria-hidden ancestor', {'attrs': {'aria-hidden': 'true'}, 'children': [alert()]}, 'no_platform_alert'),
            ('invisible alert', {'children': [alert(visible=False)]}, 'no_platform_alert'),
            ('CSS hidden', {'children': [alert(visibility='hidden')]}, 'no_platform_alert'),
            ('CSS collapsed', {'children': [alert(visibility='collapse')]}, 'no_platform_alert'),
            ('zero-width notice', {'children': [alert(width=0)]}, 'no_platform_alert'),
            ('zero-height notice', {'children': [alert(height=0)]}, 'no_platform_alert'),
            ('generic unavailable', {'children': [alert('内容不可用')]}, 'unrecognized_alert'),
            ('missing is not deleted', {'children': [alert('作品不存在')]}, 'unrecognized_alert'),
            ('extra user words', {'children': [alert('该作品已被删除 extra')]}, 'unrecognized_alert'),
            ('no notice', {'children': []}, 'no_platform_alert'),
            ('excluded duplicate does not count', {'children': [alert(), {'tag': 'article', 'children': [alert()]}]}, 'deleted_notice'),
        ]
        fixtures = [{'root': root} for _, root, _ in cases]
        expected = [result for _, _, result in cases]
        for wrong_url in (URL + '?token=private', URL + '#hash', URL + '1', 'https://other.invalid/'):
            fixtures.append({'root': {'children': [alert()]}, 'url': wrong_url})
            expected.append('wrong_page')
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, VPUB_HOME=str(Path(temp) / 'vpub'), BILI_CLI_HOME=str(Path(temp) / 'bili'))
            run = subprocess.run([node, '-e', RUNNER], input=json.dumps({'source': GONE_SNAPSHOT,
                                 'url': URL, 'fixtures': fixtures}), text=True, encoding='utf-8',
                                 capture_output=True, check=True, timeout=10, env=env)
        self.assertEqual(expected, json.loads(run.stdout))


if __name__ == '__main__':
    unittest.main()
