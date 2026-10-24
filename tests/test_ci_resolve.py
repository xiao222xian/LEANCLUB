"""Version resolution boundary tests with a deterministic fake GitHub API."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_resolve', ROOT / 'deploy/ci/resolve-release.py')
resolver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolver)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.heads = {'website': 'a' * 40, 'backend': 'b' * 40, 'adminUi': 'c' * 40}
        self.calls = []
        self.allowed_ancestors = set()
        self.repository_private = True
        self.requested = dict(resolver.BRANCHES)

    def api(self, path):
        self.calls.append(path)
        if not path:
            return {'full_name': resolver.REPOSITORY, 'private': self.repository_private}
        for component, branch in resolver.BRANCHES.items():
            if path == 'git/ref/heads/' + branch:
                return {'ref': 'refs/heads/' + branch, 'object': {'type': 'commit', 'sha': self.heads[component]}}
        if path.startswith('compare/'):
            base, head = path[len('compare/'):].split('...')
            if (base, head) in self.allowed_ancestors:
                return {'status': 'ahead', 'merge_base_commit': {'sha': base}, 'base_commit': {'sha': base}}
            return {'status': 'diverged', 'merge_base_commit': {'sha': 'f' * 40}, 'base_commit': {'sha': base}}
        self.fail('Unexpected API request ' + path)

    def resolve(self, **overrides):
        values = {'repository': resolver.REPOSITORY, 'event': 'push', 'workflow_ref': 'refs/heads/main',
                  'workflow_sha': self.heads['website'], 'tools_commit': self.heads['website'],
                  'requested': self.requested, 'api': self.api}
        values.update(overrides)
        return resolver.resolve(**values)

    def test_main_push_resolves_three_immutable_commits_once(self):
        result = self.resolve()
        self.assertEqual(result['commits'], self.heads)
        self.assertEqual(result['toolsCommit'], self.heads['website'])
        self.assertEqual(len(self.calls), 4)

    def test_platform_push_uses_event_sha_even_if_branch_advanced(self):
        old = 'd' * 40
        self.allowed_ancestors.add((old, self.heads['backend']))
        result = self.resolve(workflow_ref='refs/heads/platform/backend', workflow_sha=old)
        self.assertEqual(result['commits']['backend'], old)
        self.assertEqual(result['workflowSha'], old)
        self.assertEqual(result['toolsCommit'], self.heads['website'])

    def test_manual_old_component_commit_allowed_only_on_its_branch(self):
        old = 'd' * 40
        self.requested['adminUi'] = old
        self.allowed_ancestors.add((old, self.heads['adminUi']))
        result = self.resolve(event='workflow_dispatch')
        self.assertEqual(result['commits']['adminUi'], old)

    def test_source_from_other_component_is_rejected(self):
        self.requested['backend'] = self.heads['adminUi']
        with self.assertRaisesRegex(ValueError, 'expected component branch'):
            self.resolve(event='workflow_dispatch')

    def test_moved_main_tools_commit_must_remain_ancestor(self):
        old = 'd' * 40
        self.allowed_ancestors.add((old, self.heads['website']))
        self.assertEqual(self.resolve(tools_commit=old, event='workflow_dispatch')['toolsCommit'], old)
        with self.assertRaises(ValueError):
            self.resolve(tools_commit='e' * 40, event='workflow_dispatch')
        with self.assertRaisesRegex(ValueError, 'Main push tools'):
            self.resolve(tools_commit=old)

    def test_unexpected_repo_event_and_branches_fail_before_network(self):
        for args in ({'repository': 'xiao222xian/LEANCLUB'}, {'event': 'pull_request'},
                     {'workflow_ref': 'refs/tags/v1'}, {'workflow_ref': 'refs/heads/main-malicious'},
                     {'workflow_sha': 'abcd'}, {'event': 'workflow_dispatch', 'workflow_ref': 'refs/heads/platform/backend'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.resolve(**args)
        self.assertEqual(self.calls, [])

    def test_full_branch_refs_normalized_and_unsafe_refs_rejected(self):
        self.requested['backend'] = 'refs/heads/platform/backend'
        self.assertEqual(self.resolve()['sourceRefs']['backend'], 'platform/backend')
        for bad in ('https://github.com/evil/repo', 'main', 'HEAD', 'HEAD~1', 'b' * 7,
                    'B' * 40, 'platform/backend\nOTHER=evil', '--help', 'refs/pull/1/head'):
            with self.subTest(ref=bad), self.assertRaises(ValueError):
                resolver.source_ref('backend', bad)

    def test_public_repository_refused(self):
        self.repository_private = False
        with self.assertRaisesRegex(ValueError, 'expected private repository'):
            self.resolve()

    def test_compare_response_must_confirm_exact_base(self):
        def tampered(path):
            if path.startswith('compare/'):
                return {'status': 'ahead', 'merge_base_commit': {'sha': 'e' * 40}, 'base_commit': {'sha': 'd' * 40}}
            return self.api(path)
        with self.assertRaises(ValueError):
            self.resolve(tools_commit='d' * 40, api=tampered)


if __name__ == '__main__':
    unittest.main()
