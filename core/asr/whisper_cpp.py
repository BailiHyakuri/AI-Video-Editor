import json
import logging
from pathlib import Path
from core.asr.base import TranscriptionOptions
from core.errors import PipelineError
from core.tools import discover, ProcessRunner
from core.transcript import NormalizedTranscript, Segment, TimedText, Token


def timing(item, required=False):
    offsets = item.get('offsets', {})
    start, end = offsets.get('from'), offsets.get('to')
    if start is None or end is None or start < 0 or end < 0:
        if required:
            raise PipelineError('whisper.cpp result has missing segment timestamps.')
        return None, None
    return start / 1000, end / 1000


def read_generated_json(path):
    """Strict UTF-8, with lossless recovery only for split BPE token strings.

    whisper.cpp v1.8.2 writes byte-level vocabulary tokens separately, sometimes
    splitting a UTF-8 character between JSON strings. surrogateescape is used as
    a reversible byte carrier, never as final transcript text. All other fields
    and all reconstructed token groups must pass strict UTF-8 decoding.
    """
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode('utf-8'))
        try:
            json.dumps(data, ensure_ascii=False).encode('utf-8', errors='strict')
        except UnicodeEncodeError as exc:
            raise PipelineError(f'Generated transcript JSON {path} contains invalid Unicode escapes; '
                                'strict UTF-8 validation failed. No transcript text was discarded.') from exc
        return data
    except UnicodeDecodeError as failure:
        context = (f'Generated transcript JSON {path} failed UTF-8 decoding '
                   f'at bytes {failure.start}:{failure.end} ({raw[failure.start:failure.end].hex()})')
        try:
            data = json.loads(raw.decode('utf-8', errors='surrogateescape'))
            recovered = 0
            for segment in data.get('transcription', []):
                tokens = segment.get('tokens', [])
                repaired = []
                pending = []
                buffer = b''
                for token in tokens:
                    pending.append(token)
                    buffer += token['text'].encode('utf-8', errors='surrogateescape')
                    try:
                        text = buffer.decode('utf-8')
                    except UnicodeDecodeError as exc:
                        # Only a truncated character at the end may await more bytes.
                        if exc.reason == 'unexpected end of data' and exc.end == len(buffer):
                            continue
                        raise
                    if len(pending) == 1:
                        repaired.append(token)
                    else:
                        merged = {'text': text, 'p': None, 'components': [
                            {'token_id': part.get('id'), 'confidence': part.get('p'),
                             'start': timing(part)[0], 'end': timing(part)[1],
                             'text_bytes_hex': part['text'].encode('utf-8', errors='surrogateescape').hex()} for part in pending]}
                        offsets = [part.get('offsets', {}) for part in pending]
                        if all(o.get('from', -1) >= 0 and o.get('to', -1) >= 0 for o in offsets):
                            merged['offsets'] = {'from': offsets[0]['from'], 'to': offsets[-1]['to']}
                        repaired.append(merged)
                        recovered += 1
                    pending, buffer = [], b''
                if pending:
                    raise ValueError('Incomplete UTF-8 token sequence at segment boundary')
                if 'tokens' in segment:
                    segment['tokens'] = repaired
            # Reject surrogates in segment text, language, metadata, keys, etc.
            json.dumps(data, ensure_ascii=False).encode('utf-8', errors='strict')
            if not recovered:
                raise ValueError('Invalid bytes are not recoverable split tokens')
            logging.getLogger(__name__).info('Reassembled %d UTF-8 token groups from %s', recovered, path,
                                            extra={'event': 'token_utf8_reassembly'})
            return data
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeError) as exc:
            raise PipelineError(context + f'; lossless token reassembly failed: {exc}. '
                                'No transcript text was discarded. Use a whisper.cpp version with the UTF-8 token JSON fix.') from exc


def normalize(data):
    if not isinstance(data.get('transcription'), list):
        raise PipelineError('whisper.cpp JSON is missing transcription segments.')
    segments = []
    for index, item in enumerate(data['transcription']):
        start, end = timing(item, required=True)
        tokens = None
        if 'tokens' in item:
            tokens = []
            for token in item['tokens']:
                if token['text'].startswith('[_'):
                    continue
                t0, t1 = timing(token)
                if 'components' in token:
                    tokens.append(Token(token['text'], t0, t1, token.get('p'), token['components']))
                else:
                    tokens.append(TimedText(token['text'], t0, t1, token.get('p')))
        words = None
        if 'words' in item:
            words = [TimedText(w['text'], *timing(w), w.get('confidence', w.get('p'))) for w in item['words']]
        segments.append(Segment(text=item['text'].strip(), start=start, end=end,
                                confidence=item.get('confidence'), id=index, words=words, tokens=tokens))
    return NormalizedTranscript(data.get('result', {}).get('language'),
                                '\n'.join(s.text for s in segments if s.text), segments).validate()


class WhisperCppProvider:
    def __init__(self, model_path, binary_path=None, runner=None):
        if not model_path or not Path(model_path).expanduser().is_file():
            raise PipelineError(f'Whisper model is missing: {model_path}. Configure --model with a downloaded GGML model.')
        self.model = Path(model_path).expanduser().resolve()
        self.binary = discover('whisper-cli', binary_path)
        self.runner = runner or ProcessRunner()

    def transcribe(self, audio_path: Path, options: TranscriptionOptions):
        prefix = audio_path.parent / (audio_path.stem + '-asr')
        self.runner.run([self.binary, '-m', self.model, '-f', audio_path, '-l', options.language, '-ojf', '-of', prefix], options.timeout)
        try:
            return normalize(read_generated_json(prefix.with_suffix('.json')))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise PipelineError(f'Cannot read generated whisper.cpp transcript JSON {prefix.with_suffix(".json")} (UTF-8): {exc}') from exc
