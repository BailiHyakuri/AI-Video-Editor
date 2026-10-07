"""Phase 1 CLI; composition root for concrete tool adapters."""
import argparse
import logging
import sys
from pathlib import Path
from core.asr.whisper_cpp import WhisperCppProvider
from core.config.settings import Settings
from core.errors import PipelineError
from core.media.service import MediaService
from core.pipeline import project_inputs, transcribe_project


def parser():
    result = argparse.ArgumentParser(description='Transcribe ordered talking-head clips (Phase 1).')
    commands = result.add_subparsers(dest='command', required=True)
    for name, help_text in [('transcribe', 'Transcribe files in exactly the supplied order'),
                            ('transcribe-project', 'Transcribe Project/clips in filename order')]:
        command = commands.add_parser(name, help=help_text)
        if name == 'transcribe':
            command.add_argument('inputs', nargs='+', type=Path)
        else:
            command.add_argument('directory', type=Path)
        command.add_argument('--config', type=Path, help='JSON settings file; CLI options override it')
        command.add_argument('--name', help='Output project name (default: first video stem or directory name)')
        command.add_argument('--output-dir')
        command.add_argument('--temporary-dir')
        command.add_argument('--model', help='Path to downloaded whisper.cpp GGML model')
        command.add_argument('--whisper-binary', help='whisper-cli executable; otherwise discover on PATH')
        command.add_argument('--ffmpeg')
        command.add_argument('--ffprobe')
        command.add_argument('--language', help='Language code, or auto for detection')
        command.add_argument('--timeout', type=float, help='Timeout in seconds per external tool invocation')
        command.add_argument('--verbose', action='store_true')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        settings = Settings.load(args.config)
        for key in ('output_dir', 'temporary_dir', 'model', 'whisper_binary', 'ffmpeg', 'ffprobe', 'language', 'timeout'):
            if getattr(args, key) is not None:
                setattr(settings, key, getattr(args, key))
        if args.verbose:
            settings.log_level = 'DEBUG'
            logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
        settings.validate()
        if args.command == 'transcribe-project':
            inputs = project_inputs(args.directory)
            name = args.name or args.directory.resolve().name
        else:
            inputs = args.inputs
            name = args.name or inputs[0].stem
        # Validate paths before checking tools, for useful argument errors.
        from core.media.service import validate_input
        inputs = [validate_input(p) for p in inputs]
        media = MediaService(settings.ffmpeg, settings.ffprobe, timeout=settings.timeout)
        provider = WhisperCppProvider(settings.model, settings.whisper_binary)
        output = transcribe_project(inputs, name, settings, media, provider, progress=print)
        print(f'Transcripts saved to {output}')
        return 0
    except (PipelineError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Transcription interrupted; temporary workspace cleaned up.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
