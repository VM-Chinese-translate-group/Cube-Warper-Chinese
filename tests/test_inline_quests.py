import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts"))

import ftb_snbt_lib as snbtlib
import LangSpliter as splitter
import github2para
import para2github


class InlineQuestsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.chapters = self.root / "chapters"
        self.chapters.mkdir()
        self.output = self.root / "json"
        self.translated = self.root / "translated"
        self.lang = self.root / "lang/en_us.snbt"
        self.zh_lang = self.root / "lang/zh_cn.snbt"

    def split(self, flatten=False):
        splitter.split_and_process_all(
            str(self.lang), str(self.chapters), "", str(self.output), flatten
        )
        return {
            key: value
            for file in self.output.glob("*.json")
            for key, value in json.loads(file.read_text(encoding="utf-8")).items()
        }

    def merge(self):
        splitter.merge_all_to_snbt(
            str(self.output), str(self.zh_lang),
            str(self.chapters), str(self.translated),
        )

    def test_real_chapters_round_trip(self):
        source = ROOT / "Source/config/ftbquests/quests/chapters"
        for file in source.glob("*.snbt"):
            shutil.copyfile(file, self.chapters / file.name)
        entries = self.split()
        self.assertEqual(len(list(self.output.glob("*.json"))), 8)
        self.assertEqual(
            entries["quest.30A0ACC1C6D738A8.embedded.title"], "Getting Started"
        )
        self.assertEqual(
            entries["quest.30A0ACC1C6D738A8.embedded.description01"],
            "Board the Hexahedron and press the big red button!",
        )
        expected_count = sum(
            len(value) if isinstance(value, list) else 1
            for file in source.glob("*.snbt")
            for _, entity in splitter.iter_chapter_entities(
                snbtlib.loads(file.read_text(encoding="utf-8"))
            )
            for field in splitter.INLINE_TEXT_FIELDS
            for value in [entity.get(field)]
            if isinstance(value, (str, list))
        )
        self.assertEqual(
            sum(bool(splitter.INLINE_TEXT_KEY.fullmatch(key)) for key in entries),
            expected_count,
        )
        self.merge()
        for file in source.glob("*.snbt"):
            self.assertEqual(
                snbtlib.loads(file.read_text(encoding="utf-8")),
                snbtlib.loads((self.translated / file.name).read_text(encoding="utf-8")),
            )
        self.assertFalse(self.zh_lang.exists())

    def test_mixed_language_and_inline_translation(self):
        chapter = {
            "id": "AA", "title": "Chapter", "subtitle": "Chapter subtitle",
            "description": ["chapter line", ""],
            "quests": [{
                "id": "BB", "title": "Quest", "subtitle": "Quest subtitle",
                "description": ['Quoted "text" and C:\\path', "", "last"],
                "tasks": [{"id": "CC", "title": "Task", "subtitle": "Task subtitle",
                           "description": ["one"]}],
                "rewards": [{"id": "DD", "title": "Reward"}],
            }],
        }
        (self.chapters / "mixed.snbt").write_text(
            snbtlib.dumps(snbtlib.loads(json.dumps(chapter))), encoding="utf-8"
        )
        self.lang.parent.mkdir()
        self.lang.write_text(
            '{"quest.BB.title": "Independent language title"}', encoding="utf-8"
        )
        entries = self.split()
        self.assertEqual(entries["quest.BB.title"], "Independent language title")
        self.assertEqual(entries["quest.BB.embedded.title"], "Quest")
        for key in entries:
            if splitter.INLINE_TEXT_KEY.fullmatch(key):
                entries[key] = "译文：" + entries[key]
        (self.output / "en_us_mixed.json").write_text(
            json.dumps(entries, ensure_ascii=False), encoding="utf-8"
        )
        self.merge()
        translated = snbtlib.loads(
            (self.translated / "mixed.snbt").read_text(encoding="utf-8")
        )
        for (_, original), (_, result) in zip(
            splitter.iter_chapter_entities(chapter),
            splitter.iter_chapter_entities(translated),
        ):
            for field in splitter.INLINE_TEXT_FIELDS:
                if field not in original:
                    continue
                value = original[field]
                expected = ["译文：" + line for line in value] if isinstance(value, list) else "译文：" + value
                self.assertEqual(result[field], expected)
        self.assertEqual(
            snbtlib.loads(self.zh_lang.read_text(encoding="utf-8")),
            {"quest.BB.title": "Independent language title"},
        )

    def test_flatten_single_line_preserves_list_on_merge(self):
        (self.chapters / "single.snbt").write_text(
            '{id:"AA",title:"Title",description:["One"],quests:[]}',
            encoding="utf-8",
        )
        entries = self.split(flatten=True)
        self.assertIn("chapter.AA.embedded.description", entries)
        self.merge()
        result = snbtlib.loads(
            (self.translated / "single.snbt").read_text(encoding="utf-8")
        )
        self.assertIsInstance(result["description"], list)
        self.assertEqual(result["description"], ["One"])

    def test_upload_and_download_without_language_file(self):
        old_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, old_cwd)
        source = Path("Source/config/ftbquests/quests/chapters")
        source.mkdir(parents=True)
        (source / "chapter.snbt").write_text(
            '{id:"AA",title:"Original",quests:[{id:"BB",description:["Line"]}]}',
            encoding="utf-8",
        )
        remote_files = []

        class Client:
            max_attempts = 1

            def __init__(self, token):
                pass

            def get_files(self, project_id):
                return remote_files

            def create_file(self, project_id, local_file, remote_path):
                remote_files.append({
                    "id": len(remote_files) + 1,
                    "name": remote_path + local_file.name,
                    "entries": json.loads(local_file.read_text(encoding="utf-8")),
                })

            def get_file_translation(self, project_id, file_id):
                return [
                    {"key": key, "original": value, "translation": "已翻译", "stage": 1}
                    for key, value in remote_files[file_id - 1]["entries"].items()
                ]

        with patch.dict(os.environ, {"API_TOKEN": "test", "PROJECT_ID": "1"}), \
                patch.object(github2para, "ParaTranzClient", Client), \
                patch.object(para2github, "ParaTranzClient", Client):
            github2para.main()
            self.assertEqual(len(remote_files), 1)
            para2github.main()
        result = snbtlib.loads(
            Path("CNPack/config/ftbquests/quests/chapters/chapter.snbt").read_text(encoding="utf-8")
        )
        self.assertEqual(result["title"], "已翻译")
        self.assertEqual(result["quests"][0]["description"], ["已翻译"])
        self.assertFalse(Path("CNPack/kubejs/assets/quests").exists())

    def test_invalid_snbt_fails_instead_of_partial_success(self):
        (self.chapters / "broken.snbt").write_text("{invalid", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.split()


if __name__ == "__main__":
    unittest.main()
