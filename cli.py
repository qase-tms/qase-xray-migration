"""Command-line interface for Xray to Qase migration."""

import argparse
import json
import sys
from pathlib import Path

# Python 3.11 minimum. 3.10 reaches end of life in
# October 2026 and 3.9 already has. Fail here rather than partway into a run.
if sys.version_info < (3, 11):
    sys.exit(
        f"This migration requires Python 3.11 or newer "
        f"(found {sys.version_info.major}.{sys.version_info.minor})."
    )

from orchestrator import MigrationOrchestrator
from utils.cache_manager import CacheManager
from utils.config import ConfigError, load_config
from utils.logger import REPORT, VERSION_STRING, get_logger, log_filename, setup_logger


def _configure_logging(config, args, phase: str, cache_dir=None):
    """Logging comes from config; --log-level and --log-file still override it."""
    level = args.log_level or config.get("logging.level") or "info"
    write_to_file = config.get("logging.write_to_file") is not False
    if args.log_file:
        log_file = args.log_file
    else:
        log_dir = config.get("logging.dir") or "./logs"
        log_file = log_filename(str(config.get("prefix") or ""), log_dir)
    setup_logger(log_file=str(log_file), level=level, write_to_file=write_to_file)
    return log_file


def _finish(log_file, artifacts: str = "") -> int:
    """Print the end-of-run migration report and set the exit code.

    A phase exits 0 on success and 1 on failure. Every phase
    here catches its own errors so one bad entity cannot end the run, which
    previously meant a phase that failed every single call still exited 0: an
    extraction that could not authenticate reported success and left an empty
    cache behind. The report already knows what failed, so use it.

    Warnings do not fail the run. A skipped attachment or an unmapped status is
    a degraded migration, not a broken one, and section 7a exists precisely so
    those stay visible without being fatal. Errors do fail it.
    """
    lines = [f"qase-xray-migration v{VERSION_STRING}"]
    if artifacts:
        lines.append(artifacts)
    if log_file:
        lines.append(f"Full log: {log_file}")
    REPORT.print_report(artifacts="\n".join(lines) if lines else None)

    errors = sum(
        1 for items in REPORT.issues.values() for i in items if i["level"] == "error"
    )
    if errors:
        print(f"\n❌ {errors} error(s) during this phase, see the report above.")
        return 1
    return 0




def cmd_extract(args):
    """Run extraction phase."""
    logger = get_logger(__name__)
    
    try:
        config = load_config(args.config)
        log_file = _configure_logging(config, args, "extract")
        
        # Create orchestrator
        orchestrator = MigrationOrchestrator(config)
        
        # Run extraction
        stats = orchestrator.extract()
        
        logger.info(f"Extraction completed. Cache directory: {orchestrator.cache_manager.cache_dir}")
        return _finish(log_file, f"Cache directory: {orchestrator.cache_manager.cache_dir}")
        
    except ConfigError as e:
        print(f"❌ {e}")
        print("   Then run `python preflight.py` to validate before migrating.")
        return 1
    except Exception as e:
        logger.error(f"Extraction failed: {e}", exc_info=True)
        return 1


def cmd_transform(args):
    """Run transformation phase."""
    logger = get_logger(__name__)
    
    try:
        cache_dir = Path(args.cache)
        
        if not cache_dir.exists():
            raise ValueError(f"Cache directory does not exist: {cache_dir}")
        
        config = load_config(args.config)
        log_file = _configure_logging(config, args, "transform", cache_dir)

        if not args.config:
            # Try to load minimal config from cache metadata
            cache_manager = CacheManager(cache_dir)
            metadata = cache_manager.load_metadata()
            if metadata:
                logger.info("Using cached metadata for transformation")
        
        # Create orchestrator with existing cache
        orchestrator = MigrationOrchestrator(config, cache_dir=cache_dir, dry_run=getattr(args, 'dry_run', False))
        
        # Run transformation
        stats = orchestrator.transform()
        
        transformed_dir = cache_dir / "transformed"
        logger.info(f"Transformation completed successfully!")
        logger.info(f"Transformed data saved to: {transformed_dir}")
        logger.info(f"Statistics: {stats.get('stats', {})}")
        return _finish(log_file, f"Transformed data: {transformed_dir}")
        
    except ConfigError as e:
        print(f"❌ {e}")
        print("   Then run `python preflight.py` to validate before migrating.")
        return 1
    except Exception as e:
        logger.error(f"Transformation failed: {e}", exc_info=True)
        return 1


def cmd_load(args):
    """Run load phase."""
    logger = get_logger(__name__)
    
    try:
        cache_dir = Path(args.cache)
        
        if not cache_dir.exists():
            raise ValueError(f"Cache directory does not exist: {cache_dir}")
        
        config = load_config(args.config)
        log_file = _configure_logging(config, args, "load", cache_dir)

        if not str(config.get("qase.api_token") or "").strip():
            raise ValueError("Missing 'qase.api_token' in config")
        
        # Create orchestrator with existing cache
        orchestrator = MigrationOrchestrator(config, cache_dir=cache_dir, dry_run=getattr(args, 'dry_run', False))
        
        # Run load
        stats = orchestrator.load()
        
        logger.info("Load completed successfully!")
        logger.info(f"Statistics: {stats.get('stats', {})}")
        return _finish(log_file)
        
    except ConfigError as e:
        print(f"❌ {e}")
        print("   Then run `python preflight.py` to validate before migrating.")
        return 1
    except Exception as e:
        logger.error(f"Load failed: {e}", exc_info=True)
        return 1


def cmd_migrate(args):
    """Run all phases in sequence."""
    logger = get_logger(__name__)
    
    try:
        config = load_config(args.config)
        
        # Setup logging
        log_file = _configure_logging(config, args, "migrate")
        
        # Create orchestrator
        orchestrator = MigrationOrchestrator(config, dry_run=getattr(args, 'dry_run', False))
        
        # Run full migration
        results = orchestrator.migrate()
        
        logger.info(f"Migration completed. Cache directory: {orchestrator.cache_manager.cache_dir}")
        return _finish(log_file, f"Cache directory: {orchestrator.cache_manager.cache_dir}")
        
    except ConfigError as e:
        print(f"❌ {e}")
        print("   Then run `python preflight.py` to validate before migrating.")
        return 1
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        return 1


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Xray Cloud to Qase Migration Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract data from Xray Cloud
  python cli.py extract
  
  # Extract with custom config file
  python cli.py extract --config config.json
  
  # Transform cached data
  python cli.py transform --cache ./cache/xray_extraction_20260205_143022/
  
  # Load transformed data into Qase
  python cli.py load --cache ./cache/xray_extraction_20260205_143022/ --config config.json
  
  # Run extract, transform, and load in sequence (requires Qase credentials in config)
  python cli.py migrate
        """
    )
    
    # Global arguments
    parser.add_argument(
        "--log-level",
        choices=["error", "warn", "info", "verbose", "debug",
                 "ERROR", "WARNING", "INFO", "DEBUG"],
        default=None,
        help="Override logging.level from the config for this run"
    )
    parser.add_argument(
        "--log-file",
        help="Path to log file (default: logs/[phase].log or cache/[phase].log)"
    )
    
    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Extract command
    extract_parser = subparsers.add_parser("extract", help="Extract data from Xray Cloud")
    extract_parser.add_argument(
        "--config",
        default="config.json",
        help="Path to configuration file (default: config.json)"
    )
    extract_parser.set_defaults(func=cmd_extract)
    
    # Transform command
    transform_parser = subparsers.add_parser("transform", help="Transform cached data to Qase format")
    transform_parser.add_argument(
        "--cache",
        required=True,
        help="Path to cache directory"
    )
    transform_parser.add_argument(
        "--config",
        default="config.json",
        help="Path to configuration file (default: config.json, optional for transform)"
    )
    transform_parser.set_defaults(func=cmd_transform)
    
    # Load command
    load_parser = subparsers.add_parser("load", help="Load transformed data into Qase")
    load_parser.add_argument(
        "--cache",
        required=True,
        help="Path to cache directory"
    )
    load_parser.add_argument(
        "--config",
        default="config.json",
        help="Path to configuration file (default: config.json)"
    )
    load_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and transform everything, write nothing to Qase"
    )
    load_parser.set_defaults(func=cmd_load)
    
    # Migrate command
    migrate_parser = subparsers.add_parser("migrate", help="Run all phases: extract, transform, load")
    migrate_parser.add_argument(
        "--config",
        default="config.json",
        help="Path to configuration file (default: config.json)"
    )
    migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and transform everything, write nothing to Qase"
    )
    migrate_parser.set_defaults(func=cmd_migrate)
    
    # Parse arguments and run command
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
