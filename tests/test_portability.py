import json
import shutil
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from core.config.settings import Settings
from core.pipeline import portable_source_reference, transcribe_project
from test_phase1 import FakeMedia, FakeProvider


class PortabilityTests(unittest.TestCase):
    def test_project_relocation_and_all_persistent_references(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / 'original bundle'
            inputs = [bundle / 'inputs/中文/001.MOV', bundle / 'inputs/日本語/002.mp4']
            for source in inputs:
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b'unchanged source')
            output = transcribe_project(inputs, 'demo', Settings(output_dir=str(bundle / 'Output')),
                                        FakeMedia(), FakeProvider())
            project = json.loads((output / 'project_transcript.json').read_text())
            self.assertEqual(project['schema_version'], '1.1')
            expected = ['../../inputs/中文/001.MOV', '../../inputs/日本語/002.mp4']
            self.assertEqual([clip['source_file'] for clip in project['clips']], expected)
            for index, clip in enumerate(project['clips']):
                self.assertEqual(clip['source_path_base'], 'project')
                self.assertEqual(clip['original_filename'], inputs[index].name)
                self.assertFalse(PurePosixPath(clip['source_file']).is_absolute())
                self.assertNotIn(str(root), clip['source_file'])
                transcript = json.loads((output / 'clips' / clip['clip_id'] / 'transcript.json').read_text())
                self.assertEqual(transcript['source']['file'], clip['source_file'])
                self.assertEqual(transcript['source']['path_base'], 'project')
            self.assertEqual([s['source_file'] for s in project['segments']], expected)
            moved = root / 'relocated elsewhere'
            shutil.copytree(bundle, moved)
            moved_project = moved / 'Output/demo'
            for clip in project['clips']:
                relocated_source = moved_project.joinpath(*PurePosixPath(clip['source_file']).parts).resolve()
                self.assertTrue(relocated_source.is_relative_to(moved.resolve()))
                self.assertEqual(relocated_source.read_bytes(), b'unchanged source')
            # The same input/output layout under a different root yields identical JSON.
            generated = transcribe_project([moved / p.relative_to(bundle) for p in inputs], 'demo',
                                          Settings(output_dir=str(moved / 'rerun/../Output2')),
                                          FakeMedia(), FakeProvider())
            self.assertEqual((output / 'project_transcript.json').read_bytes(),
                             (generated / 'project_transcript.json').read_bytes())

    def test_single_clip_relative_reference(self):
        root = Path('/portable-bundle')
        reference = portable_source_reference(root / 'media/video.mov', root / 'Output/demo')
        self.assertEqual(reference, {'file': '../../media/video.mov', 'path_base': 'project'})

    def test_different_drives_require_input_root_rebinding(self):
        with patch('core.pipeline.os.path.commonpath', side_effect=ValueError('different drives')):
            reference = portable_source_reference(Path('001.mov'), Path('output'))
        self.assertEqual(reference, {'file': '001.mov', 'path_base': 'input_root'})
        self.assertFalse(PurePosixPath(reference['file']).is_absolute())

    def test_unrelated_roots_do_not_persist_machine_directories(self):
        with patch('core.pipeline.os.path.commonpath', return_value='/'):
            reference = portable_source_reference(Path('/machine-private/media/video.mov'), Path('/other/output'))
        self.assertEqual(reference, {'file': 'video.mov', 'path_base': 'input_root'})
