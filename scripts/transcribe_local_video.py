"""Offline CUDA transcription of local 16-kHz mono PCM WAVs; never falls back to CPU."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from importlib import metadata
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import wave

from fetch_asr_model import LOCK, ROOT, digest, valid


def supported_languages():
    # Vocabulary shipped with the pinned multilingual runtime, not a quality claim.
    from faster_whisper.tokenizer import _LANGUAGE_CODES
    return tuple(_LANGUAGE_CODES)


def normalize_language(value):
    if not isinstance(value, str):
        raise ValueError('Language must be a supported code or auto')
    code = value.strip().lower()
    if code != 'auto' and code not in supported_languages():
        raise ValueError('Unsupported language code: ' + repr(value) + '; use auto or a code from --list-languages')
    return code

def language_argument(value):
    try:
        return normalize_language(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error

PARAMETERS = dict(beam_size=5, temperature=0, vad_filter=False,
                  word_timestamps=True, condition_on_previous_text=False)


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def validate_timestamps(segments, duration):
    """Reject unusable timing, without mistaking timing validity for word accuracy."""
    issues = []
    previous = 0.0
    for segment in segments:
        start, end = segment['start'], segment['end']
        if not (math.isfinite(start) and math.isfinite(end) and
                0 <= start <= end <= duration + 0.1 and start >= previous - 0.02):
            issues.append({'kind': 'segment_bounds_or_order', 'segment_id': segment['id']})
        previous = end
        last_word = start
        for word in segment.get('words') or []:
            a, b = word['start'], word['end']
            if not (math.isfinite(a) and math.isfinite(b) and
                    start - 0.02 <= a <= b <= end + 0.02 and a >= last_word - 0.02):
                issues.append({'kind': 'word_bounds_or_order', 'segment_id': segment['id'], 'word': word})
            last_word = b
    return issues


def validate_input(record):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', record['id']):
        raise ValueError('Unsafe case ID')
    record = {**record, 'language': normalize_language(record.get('language', 'auto'))}
    audio = Path(record['audio']).resolve(strict=True)
    audio_hash = digest(audio)
    if record.get('expected_audio_sha256', audio_hash) != audio_hash:
        raise ValueError('Source audio checksum mismatch: ' + record['id'])
    with wave.open(str(audio), 'rb') as stream:
        if (stream.getnchannels(), stream.getframerate(), stream.getsampwidth(), stream.getcomptype()) != (1, 16000, 2, 'NONE'):
            raise ValueError('Use prepare_video_evidence.py to extract 16-kHz mono PCM16 WAV first')
        duration = stream.getnframes() / stream.getframerate()
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Empty or invalid audio duration')
    result = {**record, 'audio': str(audio), 'source_sha256': audio_hash, 'duration_seconds': duration}
    if record.get('video'):
        video = Path(record['video']).resolve(strict=True)
        video_hash = digest(video)
        if record.get('expected_video_sha256', video_hash) != video_hash:
            raise ValueError('Source video checksum mismatch: ' + record['id'])
        result.update(video=str(video), source_video_sha256=video_hash)
    return result


def gpu_snapshot():
    process = subprocess.run(['nvidia-smi', '--query-gpu=name,driver_version,memory.total,memory.used',
                              '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10)
    if process.returncode:
        return {'error': process.stderr.strip()}
    return {'all_gpu_csv': process.stdout.strip(), 'memory_scope': 'total GPU use, including other applications; sampled, not a peak measurement'}


def transcribe(records, output_directory):
    inputs = [validate_input(record) for record in records]
    if not inputs or len({r['id'] for r in inputs}) != len(inputs):
        raise ValueError('Manifest must contain at least one case and unique IDs')
    output_directory = Path(output_directory).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    receipt_path = output_directory / 'run-receipt.json'
    if receipt_path.exists() or any((output_directory / (r['id'] + '-turbo.json')).exists() for r in inputs):
        raise FileExistsError('Use a new output directory; existing results will not be overwritten')
    spec = json.loads(LOCK.read_text(encoding='utf-8'))
    model_directory = ROOT / spec['model_directory']
    for entry in spec['files']:
        if not valid(model_directory / entry['name'], entry):
            raise ValueError('Missing or unverified pinned model: ' + entry['name'] + '; run fetch_asr_model.py')
    started = time.monotonic()
    receipt = {'status': 'running', 'started_at_utc': datetime.now(timezone.utc).isoformat(),
               'model': spec, 'model_directory': str(model_directory), 'parameters': PARAMETERS,
               'initial_prompt': None, 'media_uploaded': False, 'external_asr_called': False,
               'cpu_fallback_allowed': False, 'inputs': inputs, 'jobs': []}
    write_json(receipt_path, receipt)
    try:
        for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_IMPLICIT_TOKEN', 'HF_HUB_DISABLE_TELEMETRY'):
            os.environ[key] = '1'
        from video_gpu_runtime import configure_gpu_runtime
        receipt['gpu_before'] = gpu_snapshot()
        receipt['runtime'] = configure_gpu_runtime()
        from faster_whisper import WhisperModel
        receipt['versions'] = {name: metadata.version(name) for name in
                               ('faster-whisper', 'ctranslate2', 'av', 'onnxruntime', 'tokenizers', 'huggingface-hub')}
        load_started = time.monotonic()
        model = WhisperModel(str(model_directory), device='cuda', compute_type=spec['compute_type'], local_files_only=True)
        receipt.update(actual_device=model.model.device, actual_compute_type=model.model.compute_type,
                       model_load_seconds=round(time.monotonic() - load_started, 3), gpu_after_load=gpu_snapshot())
        if receipt['actual_device'] != 'cuda' or receipt['actual_compute_type'] != spec['compute_type']:
            raise RuntimeError('Actual CUDA device/compute type does not match deployment lock')
        write_json(receipt_path, receipt)
        for record in inputs:
            job_started = time.monotonic()
            segments, info = model.transcribe(record['audio'], language=None if record['language'] == 'auto' else record['language'], **PARAMETERS)
            rows = [asdict(segment) for segment in segments]
            elapsed = round(time.monotonic() - job_started, 3)
            issues = validate_timestamps(rows, record['duration_seconds'])
            if not rows or not any(s['text'].strip() for s in rows):
                raise ValueError('No speech recognized: ' + record['id'])
            if digest(Path(record['audio'])) != record['source_sha256']:
                raise ValueError('Audio changed during inference')
            if record.get('video') and digest(Path(record['video'])) != record['source_video_sha256']:
                raise ValueError('Video changed during inference')
            result = {**record, 'model': spec['model'], 'model_repository': spec['repository'],
                      'model_revision': spec['revision'], 'device': model.model.device,
                      'compute_type': model.model.compute_type,
                      'parameters': {**PARAMETERS, 'language': record['language']}, 'initial_prompt': None,
                      'requested_language': record['language'], 'detected_language': info.language,
                      'language_probability_is_accuracy': False,
                      'segments': rows, 'transcription_info': asdict(info), 'timestamp_issues': issues,
                      'elapsed_seconds': elapsed, 'media_uploaded': False,
                      'verification': 'automatic speech transcription, not human listening or material authentication'}
            path = output_directory / (record['id'] + '-turbo.json')
            write_json(path, result)
            text_path = output_directory / (record['id'] + '-turbo.txt')
            text_path.write_text('\n'.join(f"[{r['start']:.2f}-{r['end']:.2f}] {r['text'].strip()}" for r in rows) + '\n', encoding='utf-8')
            receipt['jobs'].append({'id': record['id'], 'output': str(path), 'output_sha256': digest(path),
                                    'segment_count': len(rows), 'timestamp_issues': issues,
                                    'elapsed_seconds': elapsed, 'gpu_after': gpu_snapshot()})
            write_json(receipt_path, receipt)
            print(record['id'], len(rows), 'segments,', elapsed, 'seconds, timing issues', len(issues), flush=True)
            if issues:
                raise ValueError('Timing validation failed; raw result retained for inspection')
        receipt.update(status='completed', completed_at_utc=datetime.now(timezone.utc).isoformat(),
                       total_seconds=round(time.monotonic() - started, 3))
    except Exception as error:
        receipt.update(status='failed', error_type=type(error).__name__, error=str(error)[:1200])
        raise
    finally:
        write_json(receipt_path, receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument('--audio', type=Path)
    source.add_argument('--manifest', type=Path, help='JSON array: id, audio absolute path, language (auto or supported code; omitted means auto), optional video and expected hashes')
    parser.add_argument('--language', type=language_argument, help='auto (default), or a supported language code')
    parser.add_argument('--list-languages', action='store_true', help='List runtime language codes; does not imply validated accuracy')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if args.list_languages:
        print('auto ' + ' '.join(supported_languages()))
        parser.exit()
    if not (args.audio or args.manifest) or not args.output_dir:
        parser.error('provide --audio or --manifest, and --output-dir')
    if args.audio:
        records = [{'id': 'single-audio', 'audio': str(args.audio), 'language': args.language or 'auto'}]
    else:
        if args.language:
            parser.error('Each manifest record supplies its own language')
        records = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    transcribe(records, args.output_dir)
