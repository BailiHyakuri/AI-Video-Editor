"""CLI composition root for Phase 1 transcription and Phase 2 dry-run analysis."""
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
    result = argparse.ArgumentParser(description='Transcribe clips or analyze a dry-run edit plan (Phases 1–2).')
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
    analysis = commands.add_parser('analyze', help='Create a dry-run edit plan; never modify video')
    analysis.add_argument('transcript', type=Path, help='Phase 1 project_transcript.json')
    analysis.add_argument('--mode', choices=['conservative', 'normal', 'aggressive'])
    analysis.add_argument('--config', type=Path, help='Phase 2 JSON configuration')
    analysis.add_argument('--output-dir', type=Path, help='New output directory; default: transcript folder/edit-analysis')
    llm = analysis.add_mutually_exclusive_group()
    llm.add_argument('--no-llm', dest='llm_enabled', action='store_false')
    llm.add_argument('--llm', dest='llm_enabled', action='store_true')
    analysis.set_defaults(llm_enabled=None)
    analysis.add_argument('--provider', choices=['openai', 'openai-compatible'], help='Explicitly enable this LLM provider')
    analysis.add_argument('--model', help='Explicitly configure/enable the semantic review model')
    analysis.add_argument('--base-url', help='OpenAI-compatible endpoint; API key comes from LLM_API_KEY')
    analysis.add_argument('--timeout', type=float)
    analysis.add_argument('--retries', type=int)
    analysis.add_argument('--target-pause-duration', type=float)
    analysis.add_argument('--verbose', action='store_true')
    return result


def analyze_command(args):
    from core.analysis.pipeline import analyze_to_directory
    from core.config.analysis_settings import AnalysisSettings
    from core.logging_utils import JsonFormatter
    settings = AnalysisSettings.load(args.config)
    for name in ('mode', 'provider', 'model', 'base_url', 'timeout', 'retries', 'target_pause_duration'):
        if getattr(args, name) is not None:
            setattr(settings, name, getattr(args, name))
    if args.llm_enabled is not None:
        settings.llm_enabled = args.llm_enabled
    elif args.provider is not None or args.model is not None:
        settings.llm_enabled = True
    settings.validate()
    provider = None
    if settings.llm_enabled:
        if settings.provider == 'openai':
            from core.llm.openai_provider import OpenAIProvider
            provider = OpenAIProvider(settings)
        else:
            from core.llm.compatible_provider import OpenAICompatibleProvider
            provider = OpenAICompatibleProvider(settings)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger('core')
    previous = logger.level
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    logger.addHandler(handler)
    try:
        output, plan = analyze_to_directory(args.transcript, settings, args.output_dir, provider)
        summary = plan['summary']
        print(f"Dry-run plan: {summary['delete_actions']} DELETE, {summary['shorten_pause_actions']} SHORTEN_PAUSE, "
              f"{summary['keep_actions']} KEEP; estimated removal {summary['estimated_removed_duration']:.2f} seconds.")
        print(f'No video changed. Review {output / "edit_plan.txt"}')
        return 0
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'analyze':
            return analyze_command(args)
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
