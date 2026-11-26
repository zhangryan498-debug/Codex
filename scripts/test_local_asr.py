import hashlib
from pathlib import Path
import tempfile
import unittest
import wave

from fetch_asr_model import check_content_range, valid
from transcribe_local_video import validate_input, validate_timestamps, normalize_language, supported_languages


class LocalAsrChecks(unittest.TestCase):
    def test_all_runtime_language_codes(self):
        for code in supported_languages():
            self.assertEqual(normalize_language(code), code)
        for code in ('auto', 'zh', 'id', 'en', 'tl'):
            self.assertEqual(normalize_language(code), code)
        self.assertEqual(normalize_language(' EN '), 'en')
        for code in ('', 'xx', 'filipino-unknown', None, 42):
            with self.assertRaises(ValueError):
                normalize_language(code)

    def test_reject_wrong_resume_range(self):
        check_content_range('bytes 10-19/20', 10, 19, 20)
        for bad in (None, 'bytes 0-19/20', 'bytes 10-19/*', 'bytes 10-19/21'):
            with self.assertRaises(ValueError):
                check_content_range(bad, 10, 19, 20)

    def test_checksum_detects_equal_size_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'model.bin'
            path.write_bytes(b'abc')
            entry = {'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}
            self.assertTrue(valid(path, entry))
            path.write_bytes(b'abd')
            self.assertFalse(valid(path, entry))

    def test_input_hash_and_audio_format(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'audio.wav'
            with wave.open(str(path), 'wb') as audio:
                audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\0' * 3200)
            record = {'id': 'case', 'language': 'id', 'audio': str(path)}
            self.assertEqual(validate_input(record)['duration_seconds'], 0.1)
            for change in ({'expected_audio_sha256': '0' * 64}, {'id': '../escape'}, {'language': 'xx-unsupported'}):
                with self.assertRaises(ValueError):
                    validate_input({**record, **change})
            with wave.open(str(path), 'wb') as audio:
                audio.setparams((2, 2, 16000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\0' * 6400)
            with self.assertRaises(ValueError):
                validate_input(record)

    def test_invalid_timestamp_does_not_pass(self):
        good = [{'id': 0, 'start': 0., 'end': 1., 'words': [{'start': 0.1, 'end': 0.8}]}]
        self.assertEqual(validate_timestamps(good, 1.), [])
        for bad in ([{'id': 0, 'start': 0., 'end': float('nan')}],
                    [{'id': 0, 'start': 0., 'end': 2.}],
                    [{'id': 0, 'start': 0., 'end': 1., 'words': [{'start': 0.8, 'end': 0.7}]}]):
            self.assertTrue(validate_timestamps(bad, 1.))


if __name__ == '__main__':
    unittest.main()
