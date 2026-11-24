"""MacroKit (持宏角色的公共宏原语) 和用它的队伍代码。

- MacroKit 本身: 只有方法、不盖框架上的名字、只 import 原版 OKWW 也有的东西、不读没定义的全局名字;
- 用它的队伍: 继承写成 (MacroKit, 原生类);
- 分享: 导出时内联进 zip, 原版 OKWW 上照样能加载; 导入时一字不差的内联段收回成 import, 往返不变;
- tools/macrokit_convert.py: 只删一样的方法, 该拒绝的拒绝。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_macrokit.py
"""
import ast
import builtins
import json
import shutil
import symtable
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

from ok.util.config import Config
from src.char import CustomCharLoader as loader
from src.char.BaseChar import BaseChar
from src.combat import MacroKit as kit_module
import macrokit_convert as convert

TEAMS = ROOT / 'configs' / 'custom_teams'
KIT_FILE = ROOT / 'src' / 'combat' / 'MacroKit.py'
KIT_TREE = ast.parse(KIT_FILE.read_text(encoding='utf-8'))
KIT_CLASS = next(n for n in KIT_TREE.body if isinstance(n, ast.ClassDef) and n.name == 'MacroKit')


def kit_team_files():
    """用 MacroKit 的队伍代码: (文件, 类名)。"""
    for folder in sorted(p for p in TEAMS.iterdir() if p.is_dir() and not p.name.startswith('.')):
        for class_name in folder.name.split('__'):
            for path in (folder / f'{class_name}.py', folder / f'{class_name}{loader.TEAM_IMPORT_SUFFIX}'):
                if not path.is_file():
                    continue
                tree = ast.parse(path.read_text(encoding='utf-8'))
                cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name), None)
                if cls and any(isinstance(b, ast.Name) and b.id == 'MacroKit' for b in cls.bases):
                    yield path, class_name


class MacroKitModuleTest(unittest.TestCase):
    def test_only_methods(self):
        # 类常量/状态各队写在自己类上; MacroKit 里放了常量会被没定义它的队伍悄悄用上
        for node in KIT_CLASS.body[1:]:
            self.assertIsInstance(node, ast.FunctionDef, ast.dump(node)[:80])
        self.assertNotIn('__init__', vars(kit_module.MacroKit))

    def test_does_not_shadow_framework_names(self):
        framework = set(dir(BaseChar))
        for cls in loader._registered_char_classes().values():
            framework |= set(dir(cls))
        methods = {n.name for n in KIT_CLASS.body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(methods & framework, set(), '盖掉框架上的名字, 没重写它的队伍就悄悄换了实现')

    def test_module_imports_only_what_plain_okww_has(self):
        # 只管顶层: 导出时内联进 zip, 原版 OKWW 一加载就执行。方法里面的 import (con_now 读框架的
        # con_colors 那种) 是原来队伍代码里就有的, 包在 try 里, 读不到就退回
        allowed = set(sys.stdlib_module_names) | {'numpy', 'cv2'}
        for node in KIT_TREE.body:
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
            else:
                continue
            for name in names:
                self.assertIn(name.split('.')[0], allowed, f'{name}: 导出时内联进 zip, 原版 OKWW 上没有它')

    def test_no_undefined_globals(self):
        # 内联后和队伍代码在同一个模块里, 读了没定义的全局名字就会悄悄用上队伍代码里的同名东西
        defined = set(dir(builtins)) | set(vars(kit_module))
        missing = set()

        def visit(table):
            for symbol in table.get_symbols():
                if table.get_type() != 'module' and symbol.is_global() and symbol.is_referenced() \
                        and symbol.get_name() not in defined:
                    missing.add(f'{table.get_name()}: {symbol.get_name()}')
            for child in table.get_children():
                visit(child)

        visit(symtable.symtable(KIT_FILE.read_text(encoding='utf-8'), str(KIT_FILE), 'exec'))
        self.assertEqual(missing, set())

    def test_macro_wait_uses_precise_wait(self):
        calls = []

        class Holder(kit_module.MacroKit):
            def precise_wait(self, sec):
                calls.append(sec)

        Holder().macro_wait(0.25)
        self.assertEqual(calls, [0.25])


class KitTeamsTest(unittest.TestCase):
    def test_there_are_converted_teams(self):
        self.assertGreater(len(list(kit_team_files())), 0)

    def test_bases_and_import(self):
        for path, class_name in kit_team_files():
            with self.subTest(file=path.relative_to(TEAMS).as_posix()):
                tree = ast.parse(path.read_text(encoding='utf-8'))
                cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
                self.assertEqual(len(cls.bases), 2)
                self.assertEqual(cls.bases[0].id, 'MacroKit', 'MacroKit 要在原生类前面, 才盖得过原生类没有的那些')
                self.assertTrue(any(isinstance(n, ast.ImportFrom) and n.module == 'src.combat.MacroKit'
                                    and any(a.name == 'MacroKit' for a in n.names) for n in tree.body),
                                '顶层要有 from src.combat.MacroKit import MacroKit, 导出时才内联得进去')


class ShareTest(unittest.TestCase):
    def test_inline_then_collapse_is_identity(self):
        for path, class_name in kit_team_files():
            with self.subTest(file=path.relative_to(TEAMS).as_posix()):
                code = path.read_bytes().decode('utf-8')
                shared = loader.inline_shared_modules(code)
                tree = ast.parse(shared)
                self.assertFalse(any(isinstance(n, ast.ImportFrom) and n.module == 'src.combat.MacroKit'
                                     for n in ast.walk(tree)))
                self.assertTrue(any(isinstance(n, ast.ClassDef) and n.name == 'MacroKit' for n in tree.body))
                self.assertEqual(loader.collapse_shared_modules(shared), code)

    def test_inlined_code_loads_without_coco_module(self):
        path, class_name = next(kit_team_files())
        shared = loader.inline_shared_modules(path.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / f'{class_name}.py'
            file.write_text(shared, encoding='utf-8')
            saved = sys.modules.get('src.combat.MacroKit')
            sys.modules['src.combat.MacroKit'] = None   # 原版 OKWW: import 会失败
            try:
                cls = loader._load_class_from_file(
                    loader._registered_char_classes()[class_name], file, 'coco_ww_test_inlined_macrokit')
            finally:
                sys.modules['src.combat.MacroKit'] = saved
        self.assertEqual(cls.__mro__[1].__name__, 'MacroKit')
        self.assertIsNot(cls.__mro__[1], kit_module.MacroKit)
        self.assertTrue(hasattr(cls, 'macro_wait'))

    def test_collapse_leaves_other_versions_inline(self):
        path, _ = next(kit_team_files())
        shared = loader.inline_shared_modules(path.read_text(encoding='utf-8'))
        changed = shared.replace('class MacroKit:', 'class MacroKit:  # 别的版本', 1)
        self.assertEqual(loader.collapse_shared_modules(changed), changed)

    def test_collapse_refuses_when_code_needs_inlined_names(self):
        path, _ = next(kit_team_files())
        shared = loader.inline_shared_modules(path.read_text(encoding='utf-8'))
        # 外面用了 MacroKit 模块里没 import 出来的名字: 内联时能用, 收回后就没有了
        needs = shared + '\nLEFT_DOWN = _MOUSEEVENTF_LEFTDOWN\n'
        self.assertEqual(loader.collapse_shared_modules(needs), needs)
        # 外面另绑了模块里的名字: 内联时 MacroKit 的方法看到的是外面这个
        rebinds = shared + '\n_INPUT_MOUSE = 1\n'
        self.assertEqual(loader.collapse_shared_modules(rebinds), rebinds)

    def test_export_then_import_round_trip(self):
        path, class_name = next(kit_team_files())
        team_folder = path.parent
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'configs'
            shutil.copytree(team_folder, config / 'custom_teams' / team_folder.name)
            saved = Config.config_folder
            Config.config_folder = str(config)
            try:
                team = team_folder.name.split('__')
                archive = loader.export_custom_team(team, Path(temp) / 'out', 'Test', 'test', '楽山', '1.0')
                with zipfile.ZipFile(archive) as zf:
                    exported = zf.read(f'{class_name}.py').decode('utf-8')
                info = loader.inspect_team_archive(archive)
            finally:
                Config.config_folder = saved
        self.assertIn('class MacroKit:', exported)
        self.assertFalse(any(isinstance(n, ast.ImportFrom) and n.module == 'src.combat.MacroKit'
                             for n in ast.walk(ast.parse(exported))))
        source = (team_folder / f'{class_name}{loader.TEAM_IMPORT_SUFFIX}').read_text(encoding='utf-8')
        self.assertEqual(info['codes'][class_name], source.replace('\r\n', '\n'))


SYNTHETIC = '''import time

from src.char.Hsin import Hsin as NativeHsin


class MacroBroken(Exception):
    """某一步切人没成功 / HUD 长时间不在, 后面的按键会落在错的人身上 —— 整圈放弃, 交回框架。"""


class Hsin(NativeHsin):
    A_TAP = 0.05

    def macro_wait(self, sec):
        """旧的切片等待。"""
        if sec <= 0:
            self.task.executor.check_enabled(check_pause=False)
            return
        end = time.time() + sec
        while True:
            left = end - time.time()
            if left <= 0:
                return
            time.sleep(min(0.1, left))
            self.task.executor.check_enabled(check_pause=False)

{tap}
{need}
    def own_step(self):
        self.macro_wait(0.1)
'''

def kit_method_source(name):
    node = next(n for n in KIT_CLASS.body if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = KIT_FILE.read_text(encoding='utf-8').split('\n')
    return '\n'.join(lines[node.lineno - 1:node.end_lineno]) + '\n'


KIT_TAP = kit_method_source('tap')


def synthetic(tap=KIT_TAP):
    return SYNTHETIC.replace('{tap}', tap).replace('{need}', kit_method_source('need'))


class ConvertToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kit = convert.Kit()

    def convert(self, text, probes=()):
        return convert.convert_text(text, Path('Synthetic/Hsin.py'), 'Hsin', self.kit, set(probes))

    def test_removes_identical_and_keeps_own(self):
        new_text, report = self.convert(synthetic())
        self.assertEqual(sorted(report['removed']), ['macro_wait', 'need', 'tap'])
        self.assertEqual(report['superseded'], ['macro_wait'])
        self.assertIn('class Hsin(MacroKit, NativeHsin):', new_text)
        self.assertIn('from src.combat.MacroKit import MacroKit' + chr(10), new_text, '没人用的 MacroBroken 不用导入')
        self.assertIn('def own_step', new_text)
        self.assertNotIn('class MacroBroken', new_text)
        self.assertTrue(report['verified'])

    def test_imports_kit_names_still_used(self):
        text = synthetic().replace('self.macro_wait(0.1)', "raise MacroBroken('own')")
        new_text, report = self.convert(text)
        self.assertIn('from src.combat.MacroKit import MacroKit, MacroBroken' + chr(10), new_text)

    def test_keeps_a_different_version(self):
        own_tap = KIT_TAP.replace('self.A_TAP if hold is None', 'self.A_TAP * 2 if hold is None')
        self.assertNotEqual(own_tap, KIT_TAP)
        new_text, report = self.convert(synthetic(own_tap))
        self.assertIn('tap', report['differs'])
        self.assertIn('self.A_TAP * 2', new_text)

    def test_refuses_clashing_module_name(self):
        text = synthetic().replace('import time\n', 'import time\n_INPUT = None\n', 1)
        with self.assertRaises(convert.Skip):
            self.convert(text)

    def test_refuses_gaining_a_probed_name(self):
        with self.assertRaises(convert.Skip):
            self.convert(synthetic(), probes={'quick_switch'})

    def test_refuses_super_call_into_kit(self):
        text = synthetic().replace(
            'self.macro_wait(0.1)', 'super().quick_switch(None)')
        with self.assertRaises(convert.Skip):
            self.convert(text)

    def test_refuses_rebound_global(self):
        # 方法的 AST 一样, 但它读的全局名字 time 在队伍模块里另有绑法
        text = synthetic().replace('import time\n', 'import time as _t\ntime = _t\n', 1)
        with self.assertRaises(convert.Skip):
            self.convert(text)

    def verify(self, new_text):
        convert.verify(synthetic(), new_text, Path('Synthetic/Hsin.py'), 'Hsin', self.kit, {'macro_wait'},
                       {'MacroBroken'}, {})

    def test_verify_catches_changed_kept_method(self):
        new_text, _ = self.convert(synthetic())
        self.verify(new_text)
        with self.assertRaises(convert.Skip):
            self.verify(new_text.replace('self.macro_wait(0.1)', 'self.macro_wait(0.2)'))

    def test_verify_catches_wrong_base_order(self):
        new_text, _ = self.convert(synthetic())
        with self.assertRaises(convert.Skip):
            self.verify(new_text.replace('class Hsin(MacroKit, NativeHsin):', 'class Hsin(NativeHsin, MacroKit):'))

    def test_verify_catches_lost_name(self):
        new_text, _ = self.convert(synthetic())
        with self.assertRaises(convert.Skip):
            self.verify(new_text.replace('    A_TAP = 0.05\n', ''))


if __name__ == '__main__':
    unittest.main()
