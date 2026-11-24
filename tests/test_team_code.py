"""仓库里的队伍代码都要能加载: 语法对、定义了该有的类、类继承 BaseChar。

队伍代码跟着在线更新发出去。哪个文件加载失败, 用户那边这个角色就悄悄退回内置代码,
只在日志里报一句错 (CustomCharLoader.load_team_char_class), 所以在这里拦。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_team_code.py
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.char import CustomCharLoader as loader

TEAMS = ROOT / 'configs' / 'custom_teams'


def team_folders():
    # 和 list_custom_teams 一样不看 .xxx 临时目录和 .backup 备份
    return sorted(p for p in TEAMS.iterdir()
                  if p.is_dir() and not p.name.startswith('.') and not p.name.endswith('.backup'))


def code_files(folder, class_name):
    for path in (folder / f'{class_name}.py', folder / f'{class_name}{loader.TEAM_IMPORT_SUFFIX}'):
        if path.is_file():
            yield path


class TeamCodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.char_classes = loader._registered_char_classes()

    def test_there_are_teams(self):
        self.assertTrue(team_folders())

    def test_manifests_are_valid(self):
        for folder in team_folders():
            with self.subTest(team=folder.name):
                manifest = json.loads((folder / loader.TEAM_MANIFEST_FILE).read_text(encoding='utf-8'))
                self.assertIsInstance(manifest, dict)
                self.assertIn(manifest.get('code_mode', loader.TEAM_CODE_MODE_IMPORT),
                              (loader.TEAM_CODE_MODE_IMPORT, loader.TEAM_CODE_MODE_BUILTIN))

    def test_every_code_file_loads(self):
        loaded = 0
        for folder in team_folders():
            for class_name in folder.name.split('__'):
                if class_name not in self.char_classes:
                    with self.subTest(team=folder.name, char=class_name):
                        self.fail('队伍目录名里有框架不认识的角色')
                    continue
                for path in code_files(folder, class_name):
                    name = path.relative_to(TEAMS).as_posix()
                    with self.subTest(file=name):
                        loader._validate_character_code(path.read_text(encoding='utf-8'), class_name, path)
                        module = 'coco_ww_test_' + name.replace('/', '_').replace('.', '_')
                        cls = loader._load_class_from_file(self.char_classes[class_name], path, module)
                        self.assertEqual(cls.__name__, class_name)
                        loaded += 1
        self.assertGreater(loaded, 0)


if __name__ == '__main__':
    unittest.main()
