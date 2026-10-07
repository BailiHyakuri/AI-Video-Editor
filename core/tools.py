import logging
import shutil
import subprocess
from pathlib import Path
from core.errors import PipelineError


def discover(name, configured=None):
    candidate = str(Path(configured).expanduser()) if configured else name
    found = shutil.which(candidate)
    if not found:
        raise PipelineError(f'{name} is missing or not executable: {candidate}. Install it or configure its path.')
    return Path(found).resolve()


class ProcessRunner:
    """The single subprocess boundary; injectable in media and ASR tests."""
    def run(self, args, timeout=3600):
        try:
            result = subprocess.run([str(a) for a in args], capture_output=True,
                                    timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise PipelineError(f'{Path(args[0]).name} timed out after {timeout} seconds.') from exc
        except OSError as exc:
            raise PipelineError(f'Cannot run {args[0]}: {exc}') from exc
        result.stdout_bytes = getattr(result, 'stdout', '')
        result.stderr_bytes = getattr(result, 'stderr', '')
        for stream in ('stdout', 'stderr'):
            raw = getattr(result, stream, '')
            # Some injected test runners provide text rather than subprocess bytes.
            if isinstance(raw, bytes):
                try:
                    text = raw.decode('utf-8')
                except UnicodeDecodeError as exc:
                    logging.getLogger(__name__).warning(
                        '%s subprocess %s is not UTF-8 at byte %d; console bytes escaped (raw bytes retained).',
                        args[0], stream, exc.start, extra={'event': 'console_encoding_warning'})
                    text = raw.decode('utf-8', errors='backslashreplace')
                setattr(result, stream, text)
        if result.returncode:
            raise PipelineError(f'{Path(args[0]).name} failed (exit {result.returncode}); subprocess stderr (UTF-8, invalid bytes escaped): {result.stderr[-3000:].strip()}')
        return result
